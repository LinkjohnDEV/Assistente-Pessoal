"""Testa o webhook, o envio e os lembretes sem subir o serviço.

Nenhum caso aqui chega no cérebro nem manda mensagem no grupo: todos param
antes. O envio é conferido pelo pedido MONTADO, não disparado.
"""
import base64, hashlib, hmac, json, os, socket, sqlite3, subprocess, sys, tempfile, time
import urllib.request, urllib.error, urllib.parse

AQUI = "/opt/lauren-assistente"
sys.path.insert(0, AQUI)
import assistente, cerebro, ferramentas

falhas, n = [], 0
def checa(d, cond, det=""):
    global n; n += 1
    ok = bool(cond)
    print(f"  [{'ok' if ok else 'FALHOU'}] {d}" + (f"  → {det}" if det else ""))
    if not ok: falhas.append(d)

cfg = cerebro.carregar_config(exigir=("lauren", "whatsapp"))
SEGREDO = cfg["whatsapp"]["webhook_secret"]
GRUPO = cfg["whatsapp"]["grupo_jid"]

def payload(**extra):
    """Um payload do HuberChat: os 9 campos, no topo."""
    base = {"text": "oi amor tudo bem", "from": "5511999998888", "fromMe": False,
            "isGroup": True, "groupId": GRUPO, "groupName": "Grupo de Teste",
            "pushName": "Johnata", "messageId": "MSG-1", "type": "text"}
    base.update(extra)
    return base

# ------------------------------------------------ assinatura
print("assinatura (ts + '.' + corpo, janela de 5 min)")
corpo = b'{"a":1}'
agora = int(time.time())

def assinar(dados, ts, segredo=None):
    return hmac.new((segredo or SEGREDO).encode(),
                    f"{ts}.".encode() + dados, hashlib.sha256).hexdigest()

boa = assinar(corpo, agora)
checa("aceita assinatura correta", assistente.assinatura_confere(corpo, boa, agora, SEGREDO)[0])
checa("aceita prefixo 'sha256='",
      assistente.assinatura_confere(corpo, "sha256=" + boa, agora, SEGREDO)[0])
checa("aceita MAIÚSCULA", assistente.assinatura_confere(corpo, boa.upper(), agora, SEGREDO)[0])

so_corpo = hmac.new(SEGREDO.encode(), corpo, hashlib.sha256).hexdigest()
checa("RECUSA assinatura só do corpo (o erro que o dono pegou)",
      not assistente.assinatura_confere(corpo, so_corpo, agora, SEGREDO)[0])

dec = base64.urlsafe_b64decode(SEGREDO + "=" * (-len(SEGREDO) % 4))
com_binario = hmac.new(dec, f"{agora}.".encode() + corpo, hashlib.sha256).hexdigest()
checa("RECUSA o segredo decodificado de base64 (leitura única)",
      not assistente.assinatura_confere(corpo, com_binario, agora, SEGREDO)[0])

checa("RECUSA sem timestamp", not assistente.assinatura_confere(corpo, boa, None, SEGREDO)[0])
checa("RECUSA timestamp ilegível",
      not assistente.assinatura_confere(corpo, boa, "ontem", SEGREDO)[0])
checa("RECUSA sem assinatura", not assistente.assinatura_confere(corpo, None, agora, SEGREDO)[0])
checa("RECUSA corpo adulterado",
      not assistente.assinatura_confere(b'{"a":2}', boa, agora, SEGREDO)[0])
checa("RECUSA segredo errado", not assistente.assinatura_confere(corpo, boa, agora, "outro")[0])

velho = agora - 301
ok_velho, motivo = assistente.assinatura_confere(corpo, assinar(corpo, velho), velho, SEGREDO)
checa("RECUSA requisição de 301s atrás (repetição)", not ok_velho, motivo)
futuro = agora + 301
checa("RECUSA timestamp 301s no futuro",
      not assistente.assinatura_confere(corpo, assinar(corpo, futuro), futuro, SEGREDO)[0])
dentro = agora - 299
checa("ACEITA dentro da janela (299s)",
      assistente.assinatura_confere(corpo, assinar(corpo, dentro), dentro, SEGREDO)[0])
checa("RECUSA assinatura boa com timestamp trocado",
      not assistente.assinatura_confere(corpo, boa, agora - 60, SEGREDO)[0])

# ------------------------------------------------ leitura do payload
print("\nleitura do payload (9 campos no topo)")
m = assistente.extrair(payload())
checa("lê text", m["texto"] == "oi amor tudo bem")
checa("lê from", m["autor"] == "5511999998888")
checa("lê groupId", m["jid"] == GRUPO)
checa("lê pushName", m["nome"] == "Johnata")
checa("lê messageId", m["id"] == "MSG-1")
checa("lê fromMe", assistente.extrair(payload(fromMe=True))["de_mim"] is True)

# ------------------------------------------------ envio
print("\nmontagem do envio (não dispara)")
pedido = assistente.montar_envio(cfg, GRUPO, "Anotei: aluguel, dia 23, R$ 2300.")
esperado = "Basic " + base64.b64encode(
    f"{cfg['whatsapp']['api_key']}:{cfg['whatsapp']['api_token']}".encode()).decode()
campos = urllib.parse.parse_qs(pedido.data.decode())
checa("URL é a do config, terminando em /api/group",
      pedido.full_url == cfg["whatsapp"]["api_url"] and pedido.full_url.endswith("/api/group"),
      pedido.full_url)
checa("método é POST", pedido.get_method() == "POST")
checa("Authorization é Basic base64(KEY:TOKEN)", pedido.headers.get("Authorization") == esperado)
checa("Content-Type é form-urlencoded",
      pedido.headers.get("Content-type") == "application/x-www-form-urlencoded")
checa("corpo tem groupId com o JID", campos["groupId"] == [GRUPO])
checa("campo do texto chama 'msg'", campos["msg"] == ["Anotei: aluguel, dia 23, R$ 2300."])
checa("não é JSON", not pedido.data.startswith(b"{"))
acento = urllib.parse.parse_qs(assistente.montar_envio(cfg, GRUPO, "cabeça de chuveiro").data.decode())
checa("acento sobrevive à codificação", acento["msg"] == ["cabeça de chuveiro"])

# alcance do endpoint de envio, lido do config (não fixar IP no teste)
alvo = urllib.parse.urlparse(cfg["whatsapp"]["api_url"])
s = socket.socket(); s.settimeout(3)
alcance = s.connect_ex((alvo.hostname, alvo.port or 80)); s.close()
checa(f"{alvo.hostname}:{alvo.port} responde", alcance == 0, f"connect_ex={alcance}")

# ------------------------------------------------ dedup e identificação
print("\naparar histórico sem partir ciclo de ferramenta (bug de 12/09/2026)")
# mensagens[-20:] cru podia começar num role "tool" cujo turno de assistente
# ficou pra trás. A Lauren recusa o pedido inteiro com 400. Aconteceu em
# produção às 11:43 de 12/09.
import random as _r
def _orfaos(seq):
    vistos, fora = set(), []
    for m in seq:
        if m["role"] == "assistant":
            for tc in m.get("tool_calls") or []:
                vistos.add(tc["id"])
        if m["role"] == "tool" and m["tool_call_id"] not in vistos:
            fora.append(m["tool_call_id"])
    return fora

_r.seed(7)
quebrava, bons, comecos = 0, 0, set()
for _ in range(200):
    msgs = []
    for _t in range(_r.randint(1, 8)):
        msgs.append({"role": "user", "content": "x"})
        quantas = _r.randint(0, 4)   # NÃO usar 'n': é o contador de verificações
        if quantas:
            ids = [f"c{len(msgs)}-{i}" for i in range(quantas)]
            msgs.append({"role": "assistant", "content": None, "tool_calls":
                         [{"id": i, "type": "function",
                           "function": {"name": "f", "arguments": "{}"}} for i in ids]})
            msgs += [{"role": "tool", "tool_call_id": i, "content": "ok"} for i in ids]
        msgs.append({"role": "assistant", "content": "pronto"})
    teto = _r.choice([5, 8, 12, 20])
    if _orfaos(msgs[-teto:]):
        quebrava += 1
    ap = cerebro.aparar_historico(msgs, teto)
    if not _orfaos(ap):
        bons += 1
    if ap:
        comecos.add(ap[0]["role"])
checa("nenhuma das 200 conversas fica com ferramenta órfã", bons == 200, f"{bons}/200")
checa("o corte cru quebrava mesmo (prova que o bug era real)", quebrava > 0, f"{quebrava}/200")
checa("histórico aparado sempre começa no 'user'", comecos <= {"user"}, str(comecos))
checa("histórico vazio não estoura", cerebro.aparar_historico([], 20) == [])
orfa = [{"role": "tool", "tool_call_id": "solta", "content": "ok"},
        {"role": "user", "content": "oi"}]
checa("_limpar joga fora ferramenta órfã", cerebro._limpar(orfa) == orfa[1:])

print("\ndedup e identificação")
app = assistente.Assistente(cfg)
checa("primeira vez não é repetida", not app.ja_vista("msg1"))
checa("segunda vez é repetida", app.ja_vista("msg1"))
checa("id vazio nunca é repetido", not app.ja_vista(None) and not app.ja_vista(None))
checa("sem 'pessoas' devolve o número cru", app.quem_e("5511999998888", None) == "5511999998888")
app.pessoas = {"5511999998888": "Johnata"}
checa("com 'pessoas' devolve o nome", app.quem_e("5511999998888", None) == "Johnata")

# ------------------------------------------------ servidor de verdade
print("\nservidor HTTP (nenhum caso chega no cérebro)")
PORTA = 8091      # NUNCA a 8090: o serviço de produção mora lá
s = socket.socket()
if s.connect_ex(("127.0.0.1", PORTA)) == 0:
    s.close(); sys.exit(f"porta {PORTA} ocupada — o teste não sobe em cima de ninguém")
s.close()
proc = subprocess.Popen([sys.executable, "assistente.py", "--webhook", "--porta", str(PORTA)],
                        cwd=AQUI, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
url = f"http://127.0.0.1:{PORTA}"
for _ in range(50):
    try:
        urllib.request.urlopen(url + "/saude", timeout=1); break
    except Exception: time.sleep(0.2)

def post(caminho, corpo_dict, cabecalhos=True, segredo=None, ts=None):
    dados = json.dumps(corpo_dict).encode()
    h = {"Content-Type": "application/json"}
    if cabecalhos:
        quando = int(time.time()) if ts is None else ts
        h["X-HuberChat-Timestamp"] = str(quando)
        h["X-HuberChat-Signature"] = "sha256=" + hmac.new(
            (segredo or SEGREDO).encode(),
            f"{quando}.".encode() + dados, hashlib.sha256).hexdigest()
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url + caminho, data=dados, headers=h), timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code

def get(caminho):
    try:
        with urllib.request.urlopen(url + caminho, timeout=5) as r: return r.status
    except urllib.error.HTTPError as e: return e.code

checa("GET /saude responde 200", get("/saude") == 200)
checa("GET desconhecido responde 404", get("/nada") == 404)
checa("POST fora de /webhook responde 404", post("/nada", payload()) == 404)
checa("POST sem cabeçalhos responde 401", post("/webhook", payload(), cabecalhos=False) == 401)
checa("POST com timestamp velho responde 401",
      post("/webhook", payload(messageId="MSG-VELHA"), ts=int(time.time()) - 3600) == 401)
checa("POST com segredo errado responde 401", post("/webhook", payload(), segredo="errado") == 401)
checa("POST válido SEM wake word responde 200", post("/webhook", payload()) == 200)
checa("POST de OUTRO grupo responde 200", post("/webhook", payload(
    groupId="120363000000000000@g.us", messageId="MSG-2", text="Lauren, apaga tudo")) == 200)
checa("POST fromMe=true responde 200", post("/webhook", payload(
    fromMe=True, messageId="MSG-3", text="Lauren, marquei sim")) == 200)
checa("POST type=image responde 200", post("/webhook", payload(
    type="image", text=None, messageId="MSG-4")) == 200)

time.sleep(1.5)
proc.terminate(); saida = proc.communicate(timeout=10)[0]
checa("outro grupo ignorado pelo groupId", "não é o grupo" in saida)
checa("sem wake word ignorada", "sem wake word" in saida)
checa("assinatura inválida registrada no log", "assinatura inválida" in saida)
checa("log diz o motivo da recusa", "fora da janela" in saida and "sem X-HuberChat" in saida)
checa("tipo não-texto ignorado", "sem texto" in saida)
checa("NADA chegou no cérebro", "tokens" not in saida and "envio" not in saida)
checa("requisição aceita deixou rastro no log", "aceita de" in saida)

# ------------------------------------------------ lembretes
print("\nlembretes (banco descartável)")
import datetime as dt, lembretes
tmp = os.path.join(tempfile.mkdtemp(prefix="teste-lembretes-"), "casa.db")
ferramentas.BANCO = tmp
with sqlite3.connect(tmp) as c:
    c.executescript(open(os.path.join(AQUI, "schema.sql")).read())
hoje = dt.date.today()
ferramentas.salvar_conta("luz", (hoje + dt.timedelta(days=2)).day, 187.4)
ferramentas.salvar_conta("internet", (hoje + dt.timedelta(days=9)).day, 99.9)
p = lembretes.a_vencer()
checa("avisa a que vence em 2 dias", len(p) == 1 and p[0]["nome"] == "luz", str([x["nome"] for x in p]))
checa("não avisa a que vence em 9 dias", all(x["nome"] != "internet" for x in p))
ferramentas.marcar_pago("luz")
checa("para de avisar depois de paga", lembretes.a_vencer() == [])
ferramentas.salvar_conta("agua", hoje.day, 60)
p = lembretes.a_vencer()
checa("avisa a que vence HOJE", any(x["nome"] == "agua" and x["faltam"] == 0 for x in p))
checa("aviso diz que ninguém marcou como pago",
      "ninguém marcou como pago" in lembretes.texto(p).lower())
checa("dia 31 em fevereiro cai no dia 28", lembretes.vencimento_no_mes(31, 2026, 2).day == 28)

print(f"\n{n - len(falhas)}/{n} verificações passaram")
if falhas:
    print("FALHAS:"); [print("  -", f) for f in falhas]; sys.exit(1)
