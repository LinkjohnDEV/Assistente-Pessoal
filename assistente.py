"""assistente.py — o servidor. Recebe o webhook, roteia, responde no grupo.

Dois modos:

    python3 assistente.py --terminal     conversa pelo teclado (passo 9)
    python3 assistente.py --webhook      sobe o servidor em 0.0.0.0:8090

Sobre quem bate na porta: hoje é o painel da StarCore, que repassa o payload do
HuberChat verbatim, assinado com o mesmo esquema. Este arquivo NÃO sabe disso e
não deve saber — trata tudo como se viesse do HuberChat direto, porque um dia
vai vir. Se o webhook por grupo for ligado lá, muda a URL no HuberChat e aqui
não muda uma linha.

Defesa em profundidade: o painel só repassa mensagem que começa com "Lauren",
mas aqui se valida a assinatura, se confere o JID do grupo e se aplica a wake
word de novo. Cada camada assume que a anterior falhou.
"""

import base64
import hashlib
import hmac
import json
import logging
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cerebro
import ferramentas

TETO_HISTORICO = 20        # o banco é a memória; o contexto não precisa ser longo
TETO_CORPO = 1 * 1024 * 1024
TIMEOUT_ENVIO = 30

log = logging.getLogger("lauren")


# ------------------------------------------------------------------ banco

def registrar_historico(quem, mensagem, resposta, usadas, tin, tout):
    with sqlite3.connect(ferramentas.BANCO) as c:
        c.execute(
            "INSERT INTO historico (ts, quem, mensagem, resposta, ferramentas,"
            " tokens_in, tokens_out) VALUES (datetime('now','localtime'),?,?,?,?,?,?)",
            (quem, mensagem, resposta,
             json.dumps([f["nome"] for f in usadas], ensure_ascii=False), tin, tout),
        )


# ------------------------------------------------------------- assinatura

JANELA = 300      # segundos: fora disso, é repetição de requisição capturada


def assinatura_confere(corpo, cabecalho, timestamp, segredo, agora=None):
    """HMAC-SHA256 de "<timestamp>." + corpo CRU — o esquema do HuberChat.

    O timestamp entra no que é assinado justamente pra que uma requisição
    capturada não sirva pra sempre: fora da janela de 5 minutos, recusa.

    Chave é o segredo como texto puro, uma leitura só. Aceitar mais de uma
    esconde qual é a certa e, no dia do erro, não se sabe qual caminho falhou.

    Devolve (ok, motivo) — o motivo vai pro log quando recusa.
    """
    if not cabecalho:
        return False, "sem X-HuberChat-Signature"
    if not timestamp:
        return False, "sem X-HuberChat-Timestamp"

    ts = str(timestamp).strip()
    try:
        segundos = int(ts)
    except ValueError:
        return False, f"timestamp ilegível: {ts!r}"

    diferenca = (time.time() if agora is None else agora) - segundos
    if abs(diferenca) > JANELA:
        lado = "atrasado" if diferenca > 0 else "adiantado"
        return False, (f"fora da janela de {JANELA}s — o timestamp veio {lado} "
                       f"em {abs(diferenca):.0f}s")

    recebida = cabecalho.strip()
    if recebida.lower().startswith("sha256="):
        recebida = recebida[7:].strip()

    esperada = hmac.new(segredo.encode(), f"{ts}.".encode() + corpo,
                        hashlib.sha256).hexdigest()
    if hmac.compare_digest(esperada, recebida.lower()):
        return True, "ok"
    return False, "assinatura não confere"



# ---------------------------------------------------------------- payload
# Os 9 campos vêm no TOPO do JSON, não aninhados. Formato confirmado pelo dono
# em 07/09/2026 — o painel recebe e manda por esta API há meses.
#
#   text  from  fromMe  isGroup  groupId  groupName  pushName  messageId  type

def extrair(payload):
    return {
        "texto":      payload.get("text"),
        "autor":      payload.get("from"),
        "de_mim":     bool(payload.get("fromMe")),
        "e_grupo":    bool(payload.get("isGroup")),
        "jid":        payload.get("groupId"),
        "nome_grupo": payload.get("groupName"),
        "nome":       payload.get("pushName"),
        "id":         payload.get("messageId"),
        "tipo":       payload.get("type"),
    }


# ----------------------------------------------------------------- envio
# POST form-urlencoded com Basic auth. /api/group manda pro grupo (param
# groupId); /api/sms manda pra um número (param phone). O campo do texto chama
# `msg`. Formato confirmado pelo dono em 07/09/2026.

def montar_envio(cfg, jid, texto):
    """Monta o pedido de envio. Separado do envio pra poder ser conferido."""
    wa = cfg["whatsapp"]
    credencial = base64.b64encode(
        f"{wa['api_key']}:{wa['api_token']}".encode()).decode()
    corpo = urllib.parse.urlencode({"groupId": jid, "msg": texto}).encode()
    return urllib.request.Request(wa["api_url"], data=corpo, method="POST", headers={
        "Authorization": f"Basic {credencial}",
        "Content-Type": "application/x-www-form-urlencoded",
    })


def enviar_whatsapp(cfg, jid, texto):
    try:
        with urllib.request.urlopen(montar_envio(cfg, jid, texto),
                                    timeout=TIMEOUT_ENVIO) as r:
            log.info("envio ok [%s]: %s", r.status,
                     r.read().decode("utf-8", "replace")[:300])
            return True
    except urllib.error.HTTPError as e:
        log.error("envio falhou [%s]: %s", e.code,
                  e.read().decode("utf-8", "replace")[:300])
    except urllib.error.URLError as e:
        log.error("envio não saiu: %s", e.reason)
    return False


# ------------------------------------------------------------------ fila
# Só entra na fila mensagem que falhou ANTES de gravar qualquer coisa. Se uma
# ferramenta já rodou, reprocessar gravaria duas vezes — aí é melhor avisar e
# deixar a pessoa repetir.

ESPERA_FILA = 300          # 5 min entre tentativas
MAX_TENTATIVAS = 12        # 1 hora


def enfileirar(quem, mensagem, erro):
    with sqlite3.connect(ferramentas.BANCO) as c:
        c.execute("INSERT INTO fila (quem, mensagem, criado_em, ultimo_erro)"
                  " VALUES (?,?,datetime('now','localtime'),?)",
                  (quem, mensagem, str(erro)[:300]))
    log.warning("na fila pra tentar de novo: %r", mensagem[:60])


def girar_fila(app):
    """Roda numa thread: de tempos em tempos, tenta de novo o que ficou parado."""
    while True:
        time.sleep(ESPERA_FILA)
        try:
            with sqlite3.connect(ferramentas.BANCO) as c:
                c.row_factory = sqlite3.Row
                pendentes = [dict(l) for l in c.execute(
                    "SELECT * FROM fila ORDER BY id").fetchall()]
            for f in pendentes:
                try:
                    r = app.cerebro.responder(f["mensagem"], quem=f["quem"])
                except cerebro.ErroLauren as e:
                    if getattr(e, "status", None) in (400, 401):
                        with sqlite3.connect(ferramentas.BANCO) as c:
                            c.execute("DELETE FROM fila WHERE id = ?", (f["id"],))
                        log.error("fila: descartei %r — erro de conta (%s), não é "
                                  "transitório", f["mensagem"][:60], e.status)
                        continue
                    tent = f["tentativas"] + 1
                    with sqlite3.connect(ferramentas.BANCO) as c:
                        if tent >= MAX_TENTATIVAS:
                            c.execute("DELETE FROM fila WHERE id = ?", (f["id"],))
                        else:
                            c.execute("UPDATE fila SET tentativas = ?,"
                                      " ultima_tentativa = datetime('now','localtime'),"
                                      " ultimo_erro = ? WHERE id = ?",
                                      (tent, str(e)[:300], f["id"]))
                    if tent >= MAX_TENTATIVAS:
                        log.error("desisti de %r depois de %d tentativas",
                                  f["mensagem"][:60], tent)
                        enviar_whatsapp(app.cfg, app.grupo,
                                        f'⚠️ Não consegui responder "{f["mensagem"][:80]}" '
                                        f"— a Lauren não respondeu em 1 hora. Manda de novo?")
                    continue

                with sqlite3.connect(ferramentas.BANCO) as c:
                    c.execute("DELETE FROM fila WHERE id = ?", (f["id"],))
                log.info("da fila → %s %s", r["resposta"],
                         [x["nome"] for x in r["ferramentas"]])
                registrar_historico(f["quem"], f["mensagem"], r["resposta"],
                                    r["ferramentas"], r["tokens_in"], r["tokens_out"])
                enviar_whatsapp(app.cfg, app.grupo,
                                "⏳ (resposta atrasada)\n" + r["resposta"])
        except Exception:
            log.exception("erro girando a fila")


# --------------------------------------------------------------- servidor

class Assistente:
    def __init__(self, cfg):
        self.cfg = cfg
        self.cerebro = cerebro.Cerebro(cfg)
        self.grupo = cfg["whatsapp"]["grupo_jid"]
        self.segredo = cfg["whatsapp"]["webhook_secret"]
        self.wake = (cfg.get("assistente", {}).get("wake_word") or "lauren").lower()
        self.sem_wake = cfg.get("assistente", {}).get("responder_sem_wake", False)
        self.pessoas = cfg.get("pessoas", {})
        self.historico = []
        self.vistas = []
        self.trava = threading.Lock()

    def ja_vista(self, ident):
        """O painel pode reenviar. Processar duas vezes gravaria duas vezes."""
        if not ident:
            return False
        with self.trava:
            if ident in self.vistas:
                return True
            self.vistas.append(ident)
            del self.vistas[:-500]
            return False

    def quem_e(self, autor, nome):
        if not autor:
            return nome or "desconhecido"
        numero = "".join(ch for ch in autor if ch.isdigit())
        return self.pessoas.get(numero) or self.pessoas.get(autor) or nome or numero

    def tratar(self, payload):
        m = extrair(payload)

        if m["de_mim"]:                     # ela ouvindo a própria voz vira laço
            log.info("ignorada: mensagem da própria Lauren")
            return
        if m["jid"] != self.grupo:          # um grupo só, o resto morre em silêncio
            log.info("ignorada: groupId %r não é o grupo", m["jid"])
            return
        if m["tipo"] not in (None, "text") or not m["texto"]:
            log.info("ignorada: tipo %r sem texto", m["tipo"])
            return
        if self.ja_vista(m["id"]):
            log.info("ignorada: mensagem %s repetida", m["id"])
            return
        if not self.sem_wake and self.wake not in m["texto"].lower():
            log.info("ignorada: sem wake word")
            return

        quem = self.quem_e(m["autor"], m["nome"])
        log.info("%s [%s]: %s", quem, m["autor"], m["texto"])

        try:
            with self.trava:
                historico = list(self.historico)
            r = self.cerebro.responder(m["texto"], quem=quem, historico=historico)
        except cerebro.ErroLauren as e:
            log.error("cérebro: %s", e)
            if getattr(e, "status", None) in (400, 401):
                # 400 é erro do PEDIDO (o meu), 401 é chave recusada. Nos dois
                # casos repetir igual nunca passa — a regra é da própria Lauren:
                # 400 é do lado de quem chama, 502 é do lado dela.
                # (403 NÃO entra aqui: em 10/09/2026 a Lauren devolveu 403 "plano
                # gratuito" durante uma instabilidade e voltou 15 min depois sem
                # ninguém mexer na conta. Tratar 403 como permanente perderia
                # mensagem que a fila teria salvado.)
                enviar_whatsapp(self.cfg, self.grupo,
                                f"⚠️ Não consegui processar: {e}\n"
                                f"Sua mensagem NÃO foi registrada, e repetir igual não "
                                f"resolve — o problema é do meu lado.")
                return
            if getattr(e, "ferramentas_ja_rodadas", None):
                # já gravou alguma coisa: repetir gravaria de novo
                enviar_whatsapp(self.cfg, self.grupo,
                                f"⚠️ {e}\nParte já foi gravada — confira antes de repetir.")
            else:
                enfileirar(quem, m["texto"], e)
                # "guardei sua mensagem" soava como se já estivesse registrada.
                # Não está: está numa fila esperando a IA voltar.
                enviar_whatsapp(self.cfg, self.grupo,
                                f"⚠️ {e}\nAinda NÃO registrei. Vou tentar de novo sozinha "
                                f"nos próximos minutos e te aviso quando conseguir.")
            return

        with self.trava:
            self.historico = cerebro.aparar_historico(r["mensagens"], TETO_HISTORICO)

        log.info("→ %s %s", r["resposta"], [f["nome"] for f in r["ferramentas"]])
        registrar_historico(quem, m["texto"], r["resposta"], r["ferramentas"],
                            r["tokens_in"], r["tokens_out"])
        enviar_whatsapp(self.cfg, self.grupo, r["resposta"])


def fazer_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "lauren/1.0"

        def log_message(self, formato, *args):
            log.debug("%s %s", self.address_string(), formato % args)

        def _responder(self, codigo, texto="ok"):
            dados = texto.encode()
            self.send_response(codigo)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(dados)))
            self.end_headers()
            self.wfile.write(dados)

        def do_GET(self):
            # pra conferir que subiu, sem mexer em nada
            self._responder(200 if self.path == "/saude" else 404,
                            "vivo" if self.path == "/saude" else "não é aqui")

        def do_POST(self):
            if self.path.rstrip("/") != "/webhook":
                self._responder(404, "não é aqui")
                return

            tamanho = int(self.headers.get("Content-Length") or 0)
            if tamanho <= 0 or tamanho > TETO_CORPO:
                self._responder(400, "corpo inválido")
                return
            corpo = self.rfile.read(tamanho)

            ok, motivo = assinatura_confere(
                corpo,
                self.headers.get("X-HuberChat-Signature"),
                self.headers.get("X-HuberChat-Timestamp"),
                app.segredo,
            )
            if not ok:
                log.warning("assinatura inválida de %s — %s — descartado",
                            self.address_string(), motivo)
                self._responder(401, "assinatura inválida")
                return

            # requisição aceita SEMPRE deixa rastro: sem esta linha, um webhook
            # que funciona é indistinguível de um que nunca chegou
            log.info("aceita de %s (%d bytes)", self.address_string(), len(corpo))

            try:
                payload = json.loads(corpo)
            except json.JSONDecodeError:
                self._responder(400, "json inválido")
                return

            # responde JÁ: pensar demora, e quem espera reenvia
            self._responder(200)
            threading.Thread(target=self._trabalhar, args=(payload,), daemon=True).start()

        def _trabalhar(self, payload):
            try:
                app.tratar(payload)
            except Exception:
                log.exception("falha tratando a mensagem")

    return Handler


def webhook(porta=None):
    cfg = cerebro.carregar_config(exigir=("lauren", "whatsapp"))
    app = Assistente(cfg)
    host = cfg.get("assistente", {}).get("host", "0.0.0.0")
    porta = int(porta or cfg.get("assistente", {}).get("porta", 8090))

    servidor = ThreadingHTTPServer((host, porta), fazer_handler(app))
    log.info("escutando em %s:%s/webhook | grupo %s | modelo %s",
             host, porta, app.grupo, app.cerebro.modelo)
    if not app.pessoas:
        log.warning("config 'pessoas' vazio: quem_pediu vai gravar o pushName")
    threading.Thread(target=girar_fila, args=(app,), daemon=True).start()
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        log.info("encerrando")
        servidor.shutdown()


# ---------------------------------------------------------------- terminal

def rodar_sql(consulta):
    try:
        with sqlite3.connect(ferramentas.BANCO) as c:
            cur = c.execute(consulta)
            cols = [d[0] for d in cur.description] if cur.description else []
            linhas = cur.fetchall()
    except sqlite3.Error as e:
        print(f"  erro de SQL: {e}")
        return
    if cols:
        print("  " + " | ".join(cols))
        for l in linhas:
            print("  " + " | ".join("" if v is None else str(v) for v in l))
        print(f"  ({len(linhas)} linha{'s' if len(linhas) != 1 else ''})")


def terminal(quem="Johnata"):
    cfg = cerebro.carregar_config(exigir=("lauren",))
    c = cerebro.Cerebro(cfg)
    wake = (cfg.get("assistente", {}).get("wake_word") or "lauren").lower()
    sem_wake = cfg.get("assistente", {}).get("responder_sem_wake", False)

    print(f"modo terminal — banco: {ferramentas.BANCO}")
    print(f"modelo pedido: {c.modelo} | wake word: {wake!r} | responde sem wake: {sem_wake}")
    print("'!sql <consulta>' confere o banco. Ctrl-D encerra.\n")

    historico = []
    while True:
        try:
            linha = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not linha:
            continue
        if linha.startswith("!sql "):
            rodar_sql(linha[5:])
            continue
        if not sem_wake and wake not in linha.lower():
            print("  (ignorada: sem wake word)")
            continue

        try:
            r = c.responder(linha, quem=quem, historico=historico)
        except cerebro.ErroLauren as e:
            print(f"  ✗ {e}")
            continue

        print(f"  {r['resposta']}")
        for f in r["ferramentas"]:
            print(f"    ↳ {f['nome']}({json.dumps(f['args'], ensure_ascii=False)})"
                  f" → {json.dumps(f['resultado'], ensure_ascii=False, default=str)}")
        if not r["ferramentas"]:
            print("    ↳ (nenhuma ferramenta chamada)")
        print(f"    [modelo: {r['modelo']} | tokens: {r['tokens_in']}+{r['tokens_out']}]")

        historico = cerebro.aparar_historico(r["mensagens"], TETO_HISTORICO)
        registrar_historico(quem, linha, r["resposta"], r["ferramentas"],
                            r["tokens_in"], r["tokens_out"])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if "--terminal" in sys.argv:
            terminal()
        elif "--webhook" in sys.argv:
            # --porta N sobe noutra porta: é como o teste roda sem disputar
            # a 8090 com o serviço no ar
            porta = None
            if "--porta" in sys.argv:
                porta = sys.argv[sys.argv.index("--porta") + 1]
            webhook(porta)
        else:
            sys.exit("Use: python3 assistente.py --terminal | --webhook [--porta N]")
    except cerebro.ErroLauren as e:
        sys.exit(f"✗ {e}")
