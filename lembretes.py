"""lembretes.py — o que transforma chatbot com memória em assistente.

Roda 1x por dia no cron. Olha as contas que vencem nos próximos 3 dias e manda
no grupo as que ninguém marcou como paga NESTE mês.

    python3 lembretes.py           manda no grupo
    python3 lembretes.py --seco    só mostra na tela, não manda nada

Não usa a IA. É consulta no banco e texto pronto — não tem por que gastar cota
com isso, nem por que arriscar uma alucinação num aviso de conta.
"""

import calendar
import datetime as dt
import os
import sqlite3
import sys

import assistente
import cerebro
import ferramentas

ANTECEDENCIA = 3      # dias


def vencimento_no_mes(dia, ano, mes):
    """Dia 31 num mês de 30 cai no último dia, não no mês seguinte."""
    return dt.date(ano, mes, min(dia, calendar.monthrange(ano, mes)[1]))


def a_vencer(hoje=None, antecedencia=ANTECEDENCIA):
    """Contas ativas, não pagas na competência corrente, vencendo em até N dias."""
    hoje = hoje or dt.date.today()
    r = ferramentas.consultar_contas()
    pendentes = []
    for c in r["contas"]:
        if c["pago"] or c["dia_vencimento"] is None:
            continue
        vence = vencimento_no_mes(c["dia_vencimento"], hoje.year, hoje.month)
        faltam = (vence - hoje).days
        # faltam negativo = já venceu. Antes isso saía do aviso justo quando
        # mais importa: a conta some da lista no dia seguinte ao vencimento.
        if faltam <= antecedencia:
            pendentes.append({**c, "vence_em": vence.isoformat(), "faltam": faltam})
    return sorted(pendentes, key=lambda c: c["faltam"])


def texto(pendentes):
    linhas = []
    for c in pendentes:
        if c["faltam"] < 0:
            d = abs(c["faltam"])
            quando = f"VENCEU há {d} dia{'s' if d > 1 else ''}"
        elif c["faltam"] == 0:
            quando = "vence HOJE"
        elif c["faltam"] == 1:
            quando = f"vence dia {c['dia_vencimento']} (amanhã)"
        else:
            quando = f"vence dia {c['dia_vencimento']} (daqui a {c['faltam']} dias)"
        valor = f" — R$ {c['valor']:.2f}".replace(".", ",") if c["valor"] else ""
        sino = "🚨" if c["faltam"] < 0 else "📅"
        linhas.append(f"{sino} {c['nome'].capitalize()} {quando}{valor} — "
                      f"ninguém marcou como pago.")
    return "\n\n".join(linhas)


AVISA_A_PARTIR_DE = 80      # % do limite


def tarefas_proximas(antecedencia=ANTECEDENCIA):
    """Tarefas com data vencendo em até N dias — e as que já passaram."""
    r = ferramentas.tarefas_ver()
    return [t for t in r["tarefas"]
            if t["faltam"] is not None and t["faltam"] <= antecedencia]


def texto_tarefas(tarefas):
    """O aviso das 6h já é sobre hoje — repetir "é HOJE" em toda linha é ruído.

    Só o que NÃO é de hoje ganha marcação: atrasada, amanhã, ou faltam N dias.
    """
    def com_dono(t):
        return f"{t['tarefa']}" + (f" ({t['de_quem']})" if t["de_quem"] else "")

    de_hoje = [t for t in tarefas if t["faltam"] == 0]
    atrasadas = [t for t in tarefas if t["faltam"] < 0]
    proximas = [t for t in tarefas if t["faltam"] > 0]

    blocos = []
    if atrasadas:
        linhas = []
        for t in atrasadas:
            d = abs(t["faltam"])
            linhas.append(f"🚨 {com_dono(t)} — atrasada há {d} dia{'s' if d > 1 else ''}")
        blocos.append("\n".join(linhas))
    if de_hoje:
        blocos.append("📌 *Suas tarefas de hoje*\n\n"
                      + "\n".join(f"✅ {com_dono(t)}" for t in de_hoje))
    if proximas:
        linhas = []
        for t in proximas:
            quando = "amanhã" if t["faltam"] == 1 else f"faltam {t['faltam']} dias"
            linhas.append(f"📅 {com_dono(t)} — {quando}")
        blocos.append("\n".join(linhas))
    return "\n\n".join(blocos)


def limites_apertados(pct=AVISA_A_PARTIR_DE):
    """Categorias que já passaram de N% do teto do mês."""
    r = ferramentas.resumo()
    return [c for c in r["categorias"]
            if c.get("limite") and c.get("percentual", 0) >= pct]


def texto_limites(estourando):
    linhas = []
    for c in estourando:
        valor = f"R$ {c['gasto']:.2f}".replace(".", ",")
        teto = f"R$ {c['limite']:.2f}".replace(".", ",")
        if c["estourou"]:
            linhas.append(f"🚨 {c['categoria']}: {valor} — passou do limite de {teto}.")
        else:
            linhas.append(f"⚠️ {c['categoria']}: {valor} de {teto} "
                          f"({c['percentual']}% do limite).")
    return "\n".join(linhas)


# 5. fechamento do mês no dia 1º
def fechamento_do_mes(hoje=None):
    """No dia 1º, o resumo do mês que acabou. Sem IA — é soma e texto pronto."""
    hoje = hoje or dt.date.today()
    if hoje.day != 1:
        return None
    fim = hoje - dt.timedelta(days=1)
    comp = fim.strftime("%Y-%m")
    r = ferramentas.resumo(comp)
    if not r["categorias"] and not r["total_entrou"]:
        return None

    meses = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
             "agosto", "setembro", "outubro", "novembro", "dezembro"]

    def reais(v):
        return f"R$ {v:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")

    linhas = [f"📊 *Fechamento de {meses[fim.month - 1]}*", ""]
    for c in r["categorias"][:5]:
        marca = " 🚨 passou do limite" if c.get("estourou") else ""
        linhas.append(f"🏷️ {c['categoria']} — {reais(c['gasto'])}{marca}")
    if len(r["categorias"]) > 5:
        resto = sum(c["gasto"] for c in r["categorias"][5:])
        linhas.append(f"🏷️ outras — {reais(resto)}")
    linhas += ["", f"💸 Saiu: {reais(r['total_gasto'])}",
               f"⬆️ Entrou: {reais(r['total_entrou'])}",
               f"💰 Diferença: {reais(r['diferenca'])}"]
    return "\n".join(linhas)


# 9. o historico guarda toda mensagem e toda resposta: em dois anos são
# dezenas de milhares de linhas. O que passa de 6 meses vai pra um arquivo
# JSON ao lado do banco e sai da tabela.
MESES_NO_BANCO = 6


def arquivar_historico(meses=MESES_NO_BANCO, destino=None):
    import json
    destino = destino or os.path.join(os.path.dirname(ferramentas.BANCO), "arquivo")
    with sqlite3.connect(ferramentas.BANCO) as c:
        c.row_factory = sqlite3.Row
        velhas = [dict(l) for l in c.execute(
            "SELECT * FROM historico WHERE ts < datetime('now','localtime',?)",
            (f"-{int(meses)} months",)).fetchall()]
        if not velhas:
            return {"arquivadas": 0}
        os.makedirs(destino, exist_ok=True)
        caminho = os.path.join(destino,
                               f"historico-ate-{velhas[-1]['ts'][:10]}.json")
        with open(caminho, "w") as f:
            json.dump(velhas, f, ensure_ascii=False, indent=1)
        os.chmod(caminho, 0o600)
        c.execute("DELETE FROM historico WHERE ts < datetime('now','localtime',?)",
                  (f"-{int(meses)} months",))
    return {"arquivadas": len(velhas), "arquivo": caminho}


def texto_noite(tarefas):
    """À noite ela PERGUNTA em vez de anunciar — é o que fecha o dia.

    Anunciar de manhã e nunca mais perguntar deixa tarefa pendente pra sempre;
    foi o que aconteceu em 08/09/2026 com as três primeiras.
    """
    if not tarefas:
        return None
    quantas = len(tarefas)
    cabeca = (f"🌙 Sobrou {quantas} tarefa de hoje. Deu pra fazer?"
              if quantas == 1 else
              f"🌙 Sobraram {quantas} tarefas de hoje. Deu pra fazer alguma?")
    itens = "\n".join(f"• {t['tarefa']}" for t in tarefas)
    return (f"{cabeca}\n\n{itens}\n\n"
            f"Me diz o que já foi: *Lauren, fiz a da porta*")


def noite(seco=False):
    """Disparo do fim do dia: só o que venceu hoje ou antes e segue pendente."""
    pendentes = [t for t in tarefas_proximas() if t["faltam"] <= 0]
    aviso = texto_noite(pendentes)
    if not aviso:
        print("nada pendente hoje")
        return 0
    if seco:
        print("(seco — não enviado)\n"); print(aviso); return 0
    cfg = cerebro.carregar_config(exigir=("whatsapp",))
    ok = assistente.enviar_whatsapp(cfg, cfg["whatsapp"]["grupo_jid"], aviso)
    print("enviado" if ok else "FALHOU o envio")
    return 0 if ok else 1


def main():
    seco = "--seco" in sys.argv or "--dry-run" in sys.argv
    if "--noite" in sys.argv:
        return noite(seco)

    partes = []
    fecha = fechamento_do_mes()
    if fecha:
        partes.append(fecha)
    pendentes = a_vencer()
    if pendentes:
        partes.append(texto(pendentes))
    tarefas = tarefas_proximas()
    if tarefas:
        partes.append(texto_tarefas(tarefas))
    estourando = limites_apertados()
    if estourando:
        partes.append(texto_limites(estourando))

    if not partes:
        print("nada a avisar hoje")
        return 0

    arq = arquivar_historico()
    if arq["arquivadas"]:
        print(f"histórico: {arq['arquivadas']} linhas arquivadas em {arq['arquivo']}")

    aviso = "\n\n".join(partes)
    if seco:
        print("(seco — não enviado)\n")
        print(aviso)
        return 0

    cfg = cerebro.carregar_config(exigir=("whatsapp",))
    ok = assistente.enviar_whatsapp(cfg, cfg["whatsapp"]["grupo_jid"], aviso)
    print("enviado" if ok else "FALHOU o envio")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
