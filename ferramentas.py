"""As 8 funções que a IA pode chamar. Nada aqui sabe o que é um modelo.

Quem lembra é o banco. Este arquivo é a única porta de escrita/leitura nele.
Toda função devolve um dict com o que foi realmente gravado — é isso que
alimenta a regra de ouro: ela sempre confirma o que gravou.

Nomes de conta, itens da lista e assuntos são comparados sem diferenciar
maiúscula/minúscula e sem espaço nas pontas: "Aluguel" e "aluguel " são a
mesma coisa. O schema não muda por causa disso — a normalização é aqui.
"""

import json
import os
import uuid
import sqlite3
import re
import unicodedata

BANCO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "casa.db")


def _conn():
    c = sqlite3.connect(BANCO)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


def _norm(texto):
    return " ".join(str(texto).strip().split()).lower()


def _agora(c):
    return c.execute("SELECT datetime('now','localtime')").fetchone()[0]


def competencia_atual(c=None):
    """O mês corrente no formato 'YYYY-MM'. É sempre daqui que sai o padrão."""
    if c is not None:
        return c.execute("SELECT strftime('%Y-%m','now','localtime')").fetchone()[0]
    with _conn() as k:
        return k.execute("SELECT strftime('%Y-%m','now','localtime')").fetchone()[0]


# ---------------------------------------------------------------- contas

def salvar_conta(nome, dia, valor, quem=None):
    """Cria ou atualiza a regra da conta. Não registra pagamento nenhum."""
    nome = _norm(nome)
    if not nome:
        return {"ok": False, "erro": "nome vazio"}
    dia = int(dia)
    if not 1 <= dia <= 31:
        return {"ok": False, "erro": f"dia {dia} fora de 1..31"}
    valor = float(valor)

    with _conn() as c:
        antes = c.execute("SELECT id FROM contas WHERE nome = ?", (nome,)).fetchone()
        if antes:
            c.execute(
                "UPDATE contas SET dia_vencimento = ?, valor = ?, ativo = 1 WHERE id = ?",
                (dia, valor, antes["id"]),
            )
            conta_id, acao = antes["id"], "atualizada"
        else:
            cur = c.execute(
                "INSERT INTO contas (nome, dia_vencimento, valor, criado_em, criado_por)"
                " VALUES (?,?,?,?,?)",
                (nome, dia, valor, _agora(c), quem),
            )
            conta_id, acao = cur.lastrowid, "criada"

    return {"ok": True, "acao": acao, "id": conta_id,
            "nome": nome, "dia_vencimento": dia, "valor": valor}


def marcar_pago(nome, competencia=None, valor_pago=None, banco=None, quem=None):
    """Registra o pagamento de UM mês. Repetir o mesmo mês não duplica.

    Com `banco`, também lança o gasto — senão o saldo mente pra quem paga
    conta pelo grupo: o pagamento fica registrado e o dinheiro não sai.
    """
    nome = _norm(nome)
    with _conn() as c:
        conta = c.execute(
            "SELECT id, nome, valor FROM contas WHERE nome = ?", (nome,)
        ).fetchone()
        if not conta:
            return {"ok": False, "erro": f"conta '{nome}' não existe"}

        comp = competencia or competencia_atual(c)
        ja = c.execute(
            "SELECT pago_em FROM pagamentos WHERE conta_id = ? AND competencia = ?",
            (conta["id"], comp),
        ).fetchone()
        if ja:
            return {"ok": True, "acao": "ja_estava_pago", "nome": conta["nome"],
                    "competencia": comp, "pago_em": ja["pago_em"]}

        agora = _agora(c)
        c.execute(
            "INSERT INTO pagamentos (conta_id, competencia, pago_em, valor_pago, registrado_por)"
            " VALUES (?,?,?,?,?)",
            (conta["id"], comp, agora,
             float(valor_pago) if valor_pago is not None else conta["valor"], quem),
        )

    valor = float(valor_pago) if valor_pago is not None else conta["valor"]
    r = {"ok": True, "acao": "registrado", "nome": conta["nome"],
         "competencia": comp, "pago_em": agora, "valor_pago": valor}

    if banco and valor:
        g = _lancar(valor, f"conta: {conta['nome']}", banco,
                    "Contas e utilidades", None, quem, entrada=False)
        if g["ok"]:
            r["gasto"] = {"id": g["id"], "banco": g["banco"], "saldo": g["saldo"]}
        else:
            r["gasto_nao_lancado"] = g["erro"]
    return r


def consultar_contas(nome=None):
    """Lista as contas ativas e diz, para cada uma, se o MÊS ATUAL já foi pago.

    'Já foi pago?' é sempre pergunta sobre um mês específico — por isso o
    pago vem de uma busca em pagamentos pela competência de hoje, nunca de
    um campo marcado na conta.
    """
    with _conn() as c:
        comp = competencia_atual(c)
        hoje = int(c.execute("SELECT strftime('%d','now','localtime')").fetchone()[0])
        sql = (
            "SELECT c.id, c.nome, c.dia_vencimento, c.valor,"
            "       p.pago_em, p.valor_pago"
            "  FROM contas c"
            "  LEFT JOIN pagamentos p ON p.conta_id = c.id AND p.competencia = ?"
            " WHERE c.ativo = 1"
        )
        args = [comp]
        if nome:
            sql += " AND c.nome = ?"
            args.append(_norm(nome))
        sql += " ORDER BY c.dia_vencimento"
        linhas = c.execute(sql, args).fetchall()

    contas = []
    for l in linhas:
        dia = l["dia_vencimento"]
        contas.append({
            "id": l["id"],
            "nome": l["nome"],
            "dia_vencimento": dia,
            "valor": l["valor"],
            "competencia": comp,
            "pago": l["pago_em"] is not None,
            "pago_em": l["pago_em"],
            "valor_pago": l["valor_pago"],
            # negativo = já passou do dia neste mês
            "dias_para_vencer": (dia - hoje) if dia is not None else None,
        })
    return {"ok": True, "competencia": comp, "contas": contas}


# --------------------------------------------------------- lista de compras


def _sem_acento(texto):
    """Só pra COMPARAR. O que se grava mantém o acento."""
    return "".join(ch for ch in unicodedata.normalize("NFD", texto)
                   if unicodedata.category(ch) != "Mn")


# palavras que não ajudam a identificar item nenhum
VAZIAS = {"de", "da", "do", "das", "dos", "a", "o", "as", "os", "e",
          "com", "para", "pra", "um", "uma", "no", "na", "em"}


def _casar(texto, candidatos):
    """Escolhe uma linha por (id, texto), da mais estrita pra mais solta.

    Três tentativas: igual → um contém o outro → mesmas palavras que importam,
    sem acento e sem 'de/da/do'. A terceira existe porque "fechadura da porta"
    não é substring de "fechadura porta" — o 'da' quebra o LIKE.
    """
    texto = _norm(texto)
    for l in candidatos:
        if _norm(l[1]) == texto:
            return l
    # Pedaço tem que ser palavra inteira e ter pelo menos 4 letras. Sem isso,
    # "x" casa com "levar o lixo pra rua" e "pá" casa com "papelão" — um
    # caractere solto não identifica coisa nenhuma.
    if len(texto) >= 4:
        for l in candidatos:
            alvo = _norm(l[1])
            menor, maior = (texto, alvo) if len(texto) <= len(alvo) else (alvo, texto)
            if len(menor) >= 4 and re.search(rf"\b{re.escape(menor)}\b", maior):
                return l

    def significativas(t):
        return {_sem_acento(p) for p in _norm(t).split() if p not in VAZIAS}

    palavras = significativas(texto)
    if not palavras:
        return None
    for l in candidatos:
        do_item = significativas(l[1])
        if palavras <= do_item or do_item <= palavras:
            return l
    return None


def _achar_item(c, texto):
    """Acha um item pendente da lista pelo que a pessoa falou.

    Três tentativas, da mais estrita pra mais solta:
      1. igual
      2. um contém o outro inteiro
      3. mesmas palavras que importam, em qualquer ordem

    A 3 existe porque "fechadura da porta" não é substring de "fechadura
    porta" — o 'da' quebra o LIKE e o item some, mesmo estando ali.
    """
    linhas = c.execute("SELECT id, item FROM lista_compras WHERE comprado = 0"
                       " ORDER BY id").fetchall()
    achado = _casar(texto, [(l["id"], l["item"]) for l in linhas])
    if not achado:
        return None
    return next(l for l in linhas if l["id"] == achado[0])


def lista_add(item, onde=None, valor=None, quem=None):
    """Põe um item na lista. Se já estiver pendente, não duplica.

    `onde` separa mercado de casa — sem isso o sabão aparece junto com o
    guarda-roupa quando alguém pergunta o que falta comprar.
    `valor` é o preço estimado; os dois são OPCIONAIS e item sem valor entra
    normal, só fica de fora da soma.
    """
    item = _norm(item)
    if not item:
        return {"ok": False, "erro": "item vazio"}
    onde = _norm(onde) or None
    if valor is not None:
        try:
            valor = abs(float(valor)) or None
        except (TypeError, ValueError):
            return {"ok": False, "erro": f"valor inválido: {valor!r}"}

    with _conn() as c:
        pendente = c.execute(
            "SELECT id, onde, valor FROM lista_compras WHERE item = ? AND comprado = 0",
            (item,)).fetchone()
        if pendente:
            # já está lá: aproveita pra completar o que faltava
            novos = {}
            if onde and not pendente["onde"]:
                novos["onde"] = onde
            if valor and not pendente["valor"]:
                novos["valor"] = valor
            for k, v in novos.items():
                c.execute(f"UPDATE lista_compras SET {k} = ? WHERE id = ?", (v, pendente["id"]))
            return {"ok": True, "acao": "completado" if novos else "ja_estava_na_lista",
                    "id": pendente["id"], "item": item,
                    "onde": onde or pendente["onde"], "valor": valor or pendente["valor"]}
        cur = c.execute(
            "INSERT INTO lista_compras (item, onde, valor, quem_pediu, criado_em)"
            " VALUES (?,?,?,?,?)", (item, onde, valor, quem, _agora(c)))
    return {"ok": True, "acao": "adicionado", "id": cur.lastrowid, "item": item,
            "onde": onde, "valor": valor}


def lista_marcar_comprado(item, quem=None):
    """Marca como comprado. NUNCA apaga a linha — o histórico fica."""
    with _conn() as c:
        alvo = _achar_item(c, item)
        if not alvo:
            return {"ok": False, "erro": f"'{_norm(item)}' não está na lista de pendentes"}

        agora = _agora(c)
        c.execute(
            "UPDATE lista_compras SET comprado = 1, comprado_em = ?, comprado_por = ?"
            " WHERE id = ?", (agora, quem, alvo["id"]),
        )
    return {"ok": True, "acao": "marcado", "id": alvo["id"], "item": alvo["item"],
            "comprado_em": agora}


def lista_ver(onde=None, incluir_comprados=False):
    """O que ainda falta comprar, com o total do que tem preço estimado.

    O total diz QUANTOS itens entraram nele — senão "R$ 4.300" parece o total
    de tudo quando metade da lista está sem preço.
    """
    sql = ("SELECT id, item, onde, valor, quem_pediu, criado_em, comprado,"
           " comprado_em, comprado_por FROM lista_compras")
    onde_sql, args = [], []
    if not incluir_comprados:
        onde_sql.append("comprado = 0")
    if onde:
        # busca frouxa: "supermercado", "compras" e "mercado" acham o mesmo
        # contexto. Exigir a palavra exata faria a lista sumir por um sinônimo.
        pedido = _norm(onde)
        with _conn() as c:
            existentes = [l[0] for l in c.execute(
                "SELECT DISTINCT onde FROM lista_compras WHERE onde IS NOT NULL")]
        # contextos são poucos e controlados, então aqui vale substring solta:
        # "supermercado" acha "mercado". O risco do "pá"/"papelão" não existe
        # num conjunto de 2 ou 3 palavras.
        alvo = next((x for x in existentes
                     if pedido == x or pedido in x or x in pedido), pedido)
        onde_sql.append("onde = ?")
        args.append(alvo)
    if onde_sql:
        sql += " WHERE " + " AND ".join(onde_sql)
    sql += " ORDER BY onde IS NULL, onde, id"
    with _conn() as c:
        linhas = [dict(l) for l in c.execute(sql, args).fetchall()]

    com_preco = [l for l in linhas if l["valor"]]
    r = {"ok": True, "itens": linhas, "quantidade": len(linhas)}
    if com_preco:
        r["total_estimado"] = round(sum(l["valor"] for l in com_preco), 2)
        r["itens_com_preco"] = len(com_preco)
        r["itens_sem_preco"] = len(linhas) - len(com_preco)
    if onde:
        r["onde"] = _norm(onde)
        if not linhas:
            # "não falta nada pra casa" é enganoso quando existem itens que
            # simplesmente nunca receberam contexto — melhor dizer isso
            with _conn() as c:
                soltos = c.execute(
                    "SELECT COUNT(*) FROM lista_compras"
                    " WHERE comprado = 0 AND onde IS NULL").fetchone()[0]
            with _conn() as c:
                existentes = sorted(l[0] for l in c.execute(
                    "SELECT DISTINCT onde FROM lista_compras"
                    " WHERE onde IS NOT NULL AND comprado = 0"))
            if existentes:
                r["contextos_existentes"] = existentes
                r["aviso"] = (f"não existe contexto '{r['onde']}'. Os que existem são: "
                              + ", ".join(existentes))
            if soltos:
                r["sem_contexto"] = soltos
                r["aviso"] = (r.get("aviso", "") + f" Há {soltos} item(ns) sem "
                              f"contexto nenhum.").strip()
    else:
        r["contextos"] = sorted({l["onde"] for l in linhas if l["onde"]})
        soltos = [l for l in linhas if not l["onde"]]
        if soltos and r.get("contextos"):
            r["sem_contexto"] = len(soltos)
    return r


# ---------------------------------------------------------------- fatos

def lembrar_fato(assunto, valor, quem=None):
    """Guarda um fato solto. Mesmo assunto de novo = atualiza, não empilha."""
    assunto = _norm(assunto)
    valor = str(valor).strip()
    if not assunto or not valor:
        return {"ok": False, "erro": "assunto ou valor vazio"}

    with _conn() as c:
        antes = c.execute(
            "SELECT id, valor FROM fatos WHERE assunto = ?", (assunto,)
        ).fetchone()
        agora = _agora(c)
        if antes:
            c.execute("UPDATE fatos SET valor = ?, quem_disse = ?, atualizado_em = ?"
                      " WHERE id = ?", (valor, quem, agora, antes["id"]))
            return {"ok": True, "acao": "atualizado", "id": antes["id"],
                    "assunto": assunto, "valor": valor, "valor_anterior": antes["valor"]}
        cur = c.execute(
            "INSERT INTO fatos (assunto, valor, quem_disse, criado_em, atualizado_em)"
            " VALUES (?,?,?,?,?)", (assunto, valor, quem, agora, agora),
        )
    return {"ok": True, "acao": "criado", "id": cur.lastrowid,
            "assunto": assunto, "valor": valor}


def buscar_fato(termo):
    """Procura o termo no assunto e também no conteúdo do fato."""
    termo = _norm(termo)
    with _conn() as c:
        linhas = [dict(l) for l in c.execute(
            "SELECT id, assunto, valor, quem_disse, criado_em, atualizado_em FROM fatos"
            " WHERE assunto LIKE ? OR lower(valor) LIKE ? ORDER BY assunto",
            (f"%{termo}%", f"%{termo}%"),
        ).fetchall()]
    return {"ok": True, "termo": termo, "fatos": linhas, "quantidade": len(linhas)}


FERRAMENTAS = {
    "salvar_conta": salvar_conta,
    "marcar_pago": marcar_pago,
    "consultar_contas": consultar_contas,
    "lista_add": lista_add,
    "lista_marcar_comprado": lista_marcar_comprado,
    "lista_ver": lista_ver,
    "lembrar_fato": lembrar_fato,
    "buscar_fato": buscar_fato,
}


# =========================================================================
# DINHEIRO — bancos, movimentos e limites (07/09/2026)
#
# O saldo NUNCA é lido de um campo. É sempre SUM(valor) dos movimentos não
# estornados. Mesma lição de contas/pagamentos: estado guardado mente, evento
# somado não.
# =========================================================================

# Lista fechada de propósito. Categoria livre vira "Transporte", "transporte"
# e "Locomoção" — três linhas no relatório pra mesma coisa.
CATEGORIAS = [
    "Alimentação", "Mercado", "Transporte", "Moradia", "Saúde", "Educação",
    "Lazer", "Vestuário", "Assinaturas", "Pets", "Contas e utilidades", "Outros",
]


def _categoria(valor):
    if not valor:
        return "Outros"
    alvo = _norm(valor)
    for c in CATEGORIAS:
        if _norm(c) == alvo:
            return c
    return "Outros"


def _hoje(c):
    return c.execute("SELECT date('now','localtime')").fetchone()[0]


def _resolver_banco(c, nome, quem=None, criar=True):
    """Devolve (linha_do_banco, erro). Sem nome: usa o único que existir."""
    if nome:
        nome = _norm(nome)
        b = c.execute("SELECT * FROM bancos WHERE nome = ?", (nome,)).fetchone()
        if b:
            return b, None
        if not criar:
            return None, f"banco '{nome}' não existe"
        c.execute("INSERT INTO bancos (nome, criado_em, criado_por) VALUES (?,?,?)",
                  (nome, _agora(c), quem))
        return c.execute("SELECT * FROM bancos WHERE nome = ?", (nome,)).fetchone(), None

    todos = c.execute("SELECT * FROM bancos ORDER BY id").fetchall()
    if len(todos) == 1:
        return todos[0], None
    if not todos:
        c.execute("INSERT INTO bancos (nome, tipo, criado_em, criado_por)"
                  " VALUES ('dinheiro','dinheiro',?,?)", (_agora(c), quem))
        return c.execute("SELECT * FROM bancos WHERE nome='dinheiro'").fetchone(), None
    nomes = ", ".join(b["nome"] for b in todos)
    return None, f"tem mais de um: {nomes}. Diga de qual foi."


def _saldo(c, banco_id):
    """Só conta o que JÁ aconteceu. Parcela de novembro não sai do bolso hoje."""
    return c.execute(
        "SELECT COALESCE(SUM(valor),0) FROM movimentos"
        " WHERE banco_id = ? AND estornado = 0"
        "   AND quando <= date('now','localtime')", (banco_id,)).fetchone()[0]


def _lancar(valor, descricao, banco, categoria, quando, quem, entrada):
    try:
        valor = abs(float(valor))
    except (TypeError, ValueError):
        return {"ok": False, "erro": f"valor inválido: {valor!r}"}
    if valor == 0:
        return {"ok": False, "erro": "valor zero"}

    with _conn() as c:
        b, erro = _resolver_banco(c, banco, quem)
        if erro:
            return {"ok": False, "erro": erro}

        dia = (quando or "").strip() or _hoje(c)
        if not c.execute("SELECT date(?) IS NOT NULL", (dia,)).fetchone()[0]:
            return {"ok": False, "erro": f"data inválida: {dia!r} (use AAAA-MM-DD)"}
        dia = c.execute("SELECT date(?)", (dia,)).fetchone()[0]

        if entrada:
            cat = categoria if categoria in ("Saldo inicial",) else "Entrada"
        else:
            cat = _categoria(categoria)
        assinado = valor if entrada else -valor
        cur = c.execute(
            "INSERT INTO movimentos (banco_id, valor, descricao, categoria, quando,"
            " competencia, registrado_em, registrado_por) VALUES (?,?,?,?,?,?,?,?)",
            (b["id"], assinado, (descricao or "").strip() or None, cat, dia,
             dia[:7], _agora(c), quem),
        )
        return {"ok": True, "id": cur.lastrowid, "banco": b["nome"],
                "valor": valor, "tipo": "entrada" if entrada else "gasto",
                "descricao": (descricao or "").strip() or None, "categoria": cat,
                "quando": dia, "saldo": round(_saldo(c, b["id"]), 2)}


def banco_salvar(nome, tipo=None, saldo_inicial=None, quem=None):
    """Cadastra um banco. Com saldo_inicial > 0, lança o movimento de abertura.

    Saldo ZERO é motivo legítimo pra cadastrar ("abri a conta, tá zerada") —
    só não gera movimento de abertura, porque não há o que abrir.

    O valor é validado ANTES de inserir o banco. Antes não era, e dava o pior
    tipo de erro: o banco entrava no SQLite e a resposta dizia que não tinha
    entrado. Mensagem e banco discordando é pior que os dois errados juntos.
    """
    nome = _norm(nome)
    if not nome:
        return {"ok": False, "erro": "nome vazio"}
    tipo = (tipo or "conta").strip().lower().replace("ã", "a")
    if tipo not in ("conta", "cartao", "dinheiro"):
        tipo = "conta"

    abertura = None
    if saldo_inicial is not None:
        try:
            abertura = float(saldo_inicial)
        except (TypeError, ValueError):
            return {"ok": False, "erro": f"saldo inválido: {saldo_inicial!r}"}

    with _conn() as c:
        existia = c.execute("SELECT id FROM bancos WHERE nome = ?", (nome,)).fetchone()
        if existia:
            banco_id, acao = existia["id"], "ja_existia"
        else:
            banco_id = c.execute(
                "INSERT INTO bancos (nome, tipo, criado_em, criado_por) VALUES (?,?,?,?)",
                (nome, tipo, _agora(c), quem)).lastrowid
            acao = "criado"
        saldo = _saldo(c, banco_id)

    if abertura and acao == "criado":        # 0 e None não abrem movimento
        r = _lancar(abs(abertura), "saldo inicial", nome, "Saldo inicial", None,
                    quem, entrada=abertura > 0)
        if not r["ok"]:
            return r
        saldo = r["saldo"]

    r = {"ok": True, "acao": acao, "id": banco_id, "nome": nome,
         "tipo": tipo, "saldo": round(saldo, 2)}
    if acao == "ja_existia":
        # sem isto o modelo tenta mexer no saldo por aqui e não consegue
        r["dica"] = ("este banco já existe e esta ferramenta NÃO mexe no saldo. "
                     "Para dinheiro entrando use entrada_registrar; "
                     "para dinheiro saindo, gasto_registrar.")
    return r


def gasto_registrar(valor, descricao=None, banco=None, categoria=None,
                    quando=None, quem=None):
    """Registra uma SAÍDA de dinheiro."""
    return _lancar(valor, descricao, banco, categoria, quando, quem, entrada=False)


def entrada_registrar(valor, descricao=None, banco=None, quando=None, quem=None):
    """Registra uma ENTRADA de dinheiro (salário, pix recebido, devolução)."""
    return _lancar(valor, descricao, banco, None, quando, quem, entrada=True)


def saldo_ver(banco=None):
    """Quanto tem. Sem banco, lista todos e o total."""
    with _conn() as c:
        sql = ("SELECT b.id, b.nome, b.tipo,"
               " COALESCE((SELECT SUM(m.valor) FROM movimentos m"
               "           WHERE m.banco_id = b.id AND m.estornado = 0"
               "             AND m.quando <= date('now','localtime')),0) AS saldo"
               "  FROM bancos b")
        args = []
        if banco:
            sql += " WHERE b.nome = ?"
            args.append(_norm(banco))
        linhas = c.execute(sql + " ORDER BY b.nome", args).fetchall()

    if banco and not linhas:
        return {"ok": False, "erro": f"banco '{_norm(banco)}' não existe"}
    bancos = [{"nome": l["nome"], "tipo": l["tipo"], "saldo": round(l["saldo"], 2)}
              for l in linhas]
    return {"ok": True, "bancos": bancos,
            "total": round(sum(b["saldo"] for b in bancos), 2)}


def extrato(banco=None, competencia=None, categoria=None, limite=15):
    """Os últimos movimentos — onde o dinheiro foi."""
    with _conn() as c:
        comp = competencia or competencia_atual(c)
        sql = ("SELECT m.id, b.nome AS banco, m.valor, m.descricao, m.categoria,"
               " m.quando, m.registrado_por"
               "  FROM movimentos m JOIN bancos b ON b.id = m.banco_id"
               " WHERE m.estornado = 0 AND m.competencia = ?")
        args = [comp]
        if banco:
            sql += " AND b.nome = ?"
            args.append(_norm(banco))
        if categoria:
            sql += " AND m.categoria = ?"
            args.append(_categoria(categoria))
        sql += " ORDER BY m.quando DESC, m.id DESC LIMIT ?"
        args.append(int(limite))
        linhas = [dict(l) for l in c.execute(sql, args).fetchall()]

    return {"ok": True, "competencia": comp, "movimentos": linhas,
            "quantidade": len(linhas)}


def resumo(competencia=None, comparar_com=None):
    """Total por categoria no mês, com o limite ao lado se houver.

    Com `comparar_com`, traz o mesmo do outro mês e a variação — que é a
    pergunta que as pessoas realmente fazem: "gastei mais que mês passado?"
    """
    with _conn() as c:
        comp = competencia or competencia_atual(c)
        cats = c.execute(
            "SELECT m.categoria, SUM(-m.valor) AS gasto, COUNT(*) AS n"
            "  FROM movimentos m"
            " WHERE m.estornado = 0 AND m.competencia = ? AND m.valor < 0"
            " GROUP BY m.categoria ORDER BY gasto DESC", (comp,)).fetchall()
        # 'Saldo inicial' fica de fora: abertura de conta não é dinheiro que
        # entrou no mês — misturar os dois faz o resumo mentir no primeiro mês
        entrou = c.execute(
            "SELECT COALESCE(SUM(valor),0) FROM movimentos"
            " WHERE estornado = 0 AND competencia = ? AND valor > 0"
            "   AND COALESCE(categoria,'') <> 'Saldo inicial'", (comp,)).fetchone()[0]
        tetos = {l["categoria"]: l["valor_mes"]
                 for l in c.execute("SELECT categoria, valor_mes FROM limites")}

    linhas = []
    for l in cats:
        item = {"categoria": l["categoria"], "gasto": round(l["gasto"], 2),
                "lancamentos": l["n"]}
        if l["categoria"] in tetos:
            teto = tetos[l["categoria"]]
            item["limite"] = teto
            item["percentual"] = round(100 * l["gasto"] / teto) if teto else None
            item["estourou"] = l["gasto"] > teto
        linhas.append(item)

    saiu = sum(l["gasto"] for l in linhas)
    r = {"ok": True, "competencia": comp, "categorias": linhas,
         "total_gasto": round(saiu, 2), "total_entrou": round(entrou, 2),
         "diferenca": round(entrou - saiu, 2)}

    if comparar_com:
        outro = resumo(comparar_com)
        antes = {c["categoria"]: c["gasto"] for c in outro["categorias"]}
        for c in r["categorias"]:
            velho = antes.get(c["categoria"])
            if velho:
                c["mes_anterior"] = velho
                c["variacao_pct"] = round(100 * (c["gasto"] - velho) / velho)
        r["comparado_com"] = {
            "competencia": outro["competencia"],
            "total_gasto": outro["total_gasto"],
            "variacao_pct": (round(100 * (saiu - outro["total_gasto"]) / outro["total_gasto"])
                             if outro["total_gasto"] else None),
            "categorias_so_no_outro": [c["categoria"] for c in outro["categorias"]
                                       if c["categoria"] not in
                                       {x["categoria"] for x in r["categorias"]}],
        }
    return r


def estornar(id, quem=None):
    """Desfaz um lançamento. NÃO apaga: marca estornado e some da soma."""
    with _conn() as c:
        m = c.execute("SELECT m.*, b.nome AS banco FROM movimentos m"
                      " JOIN bancos b ON b.id = m.banco_id WHERE m.id = ?",
                      (int(id),)).fetchone()
        if not m:
            return {"ok": False, "erro": f"lançamento {id} não existe"}
        if m["estornado"]:
            return {"ok": True, "acao": "ja_estava_estornado", "id": m["id"]}
        c.execute("UPDATE movimentos SET estornado = 1 WHERE id = ?", (m["id"],))
        return {"ok": True, "acao": "estornado", "id": m["id"], "banco": m["banco"],
                "valor": abs(m["valor"]), "descricao": m["descricao"],
                "saldo": round(_saldo(c, m["banco_id"]), 2)}


def limite_definir(categoria, valor_mes, quem=None):
    """Teto de gasto de uma categoria por mês."""
    cat = _categoria(categoria)
    if cat == "Outros" and _norm(categoria or "") != "outros":
        return {"ok": False, "erro": f"categoria '{categoria}' não existe",
                "categorias": CATEGORIAS}
    try:
        teto = abs(float(valor_mes))
    except (TypeError, ValueError):
        return {"ok": False, "erro": f"valor inválido: {valor_mes!r}"}

    with _conn() as c:
        antes = c.execute("SELECT id FROM limites WHERE categoria = ?", (cat,)).fetchone()
        if antes:
            c.execute("UPDATE limites SET valor_mes = ? WHERE id = ?", (teto, antes["id"]))
            acao = "atualizado"
        else:
            c.execute("INSERT INTO limites (categoria, valor_mes, criado_em, criado_por)"
                      " VALUES (?,?,?,?)", (cat, teto, _agora(c), quem))
            acao = "criado"
        gasto = c.execute(
            "SELECT COALESCE(SUM(-valor),0) FROM movimentos"
            " WHERE estornado = 0 AND categoria = ? AND valor < 0"
            "   AND competencia = strftime('%Y-%m','now','localtime')", (cat,)).fetchone()[0]

    return {"ok": True, "acao": acao, "categoria": cat, "limite": teto,
            "gasto_no_mes": round(gasto, 2)}


def lista_corrigir(item, novo_nome=None, onde=None, valor=None, quem=None):
    """Troca nome, contexto e/ou preço de um item. Não mexe em comprado.

    Existe porque sem ela o modelo improvisa: marca o item errado como
    comprado e cria outro — e aí o banco guarda uma compra que não houve.
    """
    if novo_nome is None and onde is None and valor is None:
        return {"ok": False, "erro": "não disse o que corrigir"}
    with _conn() as c:
        alvo = _achar_item(c, item)
        if not alvo:
            return {"ok": False, "erro": f"'{_norm(item)}' não está na lista de pendentes"}
        campos, args = [], []
        if novo_nome is not None:
            novo = _norm(novo_nome)
            if not novo:
                return {"ok": False, "erro": "nome novo vazio"}
            campos.append("item = ?"); args.append(novo)
        if onde is not None:
            campos.append("onde = ?"); args.append(_norm(onde) or None)
        if valor is not None:
            try:
                campos.append("valor = ?"); args.append(abs(float(valor)) or None)
            except (TypeError, ValueError):
                return {"ok": False, "erro": f"valor inválido: {valor!r}"}
        c.execute(f"UPDATE lista_compras SET {', '.join(campos)} WHERE id = ?",
                  (*args, alvo["id"]))
        agora = c.execute("SELECT item, onde, valor FROM lista_compras WHERE id = ?",
                          (alvo["id"],)).fetchone()
    return {"ok": True, "acao": "corrigido", "id": alvo["id"], "antes": alvo["item"],
            "item": agora["item"], "onde": agora["onde"], "valor": agora["valor"]}


FERRAMENTAS.update({
    "lista_corrigir": lista_corrigir,
    "banco_salvar": banco_salvar,
    "gasto_registrar": gasto_registrar,
    "entrada_registrar": entrada_registrar,
    "saldo_ver": saldo_ver,
    "extrato": extrato,
    "resumo": resumo,
    "estornar": estornar,
    "limite_definir": limite_definir,
})


# =========================================================================
# TAREFAS (07/09/2026)
# Criar e corrigir nasceram juntas de propósito — a lição mais cara do dia
# foi que ferramenta de correção faltando faz o modelo improvisar e gravar
# dado falso.
# =========================================================================

def _achar_tarefa(c, texto):
    linhas = c.execute("SELECT id, tarefa, quando FROM tarefas WHERE feita = 0"
                       " ORDER BY id").fetchall()
    achado = _casar(texto, [(l["id"], l["tarefa"]) for l in linhas])
    if not achado:
        return None
    return next(l for l in linhas if l["id"] == achado[0])


def _data_ok(c, dia):
    """Valida AAAA-MM-DD. Devolve a data normalizada ou None."""
    if not dia:
        return None
    dia = str(dia).strip()
    r = c.execute("SELECT date(?)", (dia,)).fetchone()[0]
    return r


REPETICOES = ("diaria", "semanal", "mensal")


def _proxima_data(c, quando, repete):
    """A próxima ocorrência, a partir da data da que acabou de ser cumprida."""
    base = quando or _hoje(c)
    passo = {"diaria": "+1 day", "semanal": "+7 days", "mensal": "+1 month"}[repete]
    # se a data já ficou pra trás (tarefa cumprida atrasada), avança até o futuro
    prox = c.execute("SELECT date(?,?)", (base, passo)).fetchone()[0]
    while prox < _hoje(c):
        prox = c.execute("SELECT date(?,?)", (prox, passo)).fetchone()[0]
    return prox


def tarefa_add(tarefa, quando=None, de_quem=None, repete=None, quem=None):
    """Uma coisa a fazer. Com data, entra no aviso diário; sem data, é só lista.

    Com `repete`, quando for marcada feita nasce a próxima automaticamente.
    """
    tarefa = " ".join(str(tarefa).strip().split())
    if not tarefa:
        return {"ok": False, "erro": "tarefa vazia"}

    with _conn() as c:
        # o lista_add já recusava duplicata; a tarefa não recusava, e aí uma
        # resposta como "fazer a troca da conta de água" virava tarefa nova
        # em vez de marcar a que já existia
        igual = _achar_tarefa(c, tarefa)
        if igual:
            return {"ok": False, "acao": "ja_existe", "id": igual["id"],
                    "tarefa": igual["tarefa"], "quando": igual["quando"],
                    "erro": f"já existe a tarefa pendente '{igual['tarefa']}' (#{igual['id']}). "
                            f"Se ela foi feita, use tarefa_feita."}
        dia = None
        if quando:
            dia = _data_ok(c, quando)
            if not dia:
                return {"ok": False, "erro": f"data inválida: {quando!r} (use AAAA-MM-DD)"}
        rep = (repete or "").strip().lower() or None
        if rep and rep not in REPETICOES:
            return {"ok": False, "erro": f"repetição inválida: {repete!r}",
                    "aceitas": list(REPETICOES)}
        if rep and not dia:
            dia = _hoje(c)          # recorrente sem data começa hoje

        cur = c.execute(
            "INSERT INTO tarefas (tarefa, quando, de_quem, criado_em, criado_por, repete)"
            " VALUES (?,?,?,?,?,?)", (tarefa, dia, de_quem, _agora(c), quem, rep))
        return {"ok": True, "acao": "criada", "id": cur.lastrowid,
                "tarefa": tarefa, "quando": dia, "de_quem": de_quem, "repete": rep}


def tarefas_ver(incluir_feitas=False, ate=None):
    """O que falta fazer. Sem data vem por último."""
    with _conn() as c:
        sql = ("SELECT id, tarefa, quando, de_quem, feita, feita_em, feita_por, repete,"
               " CASE WHEN quando IS NULL THEN NULL"
               "      ELSE CAST(julianday(quando) - julianday(date('now','localtime'))"
               "           AS INTEGER) END AS faltam"
               "  FROM tarefas")
        args, onde = [], []
        if not incluir_feitas:
            onde.append("feita = 0")
        if ate:
            limite = _data_ok(c, ate)
            if not limite:
                return {"ok": False, "erro": f"data inválida: {ate!r}"}
            onde.append("quando IS NOT NULL AND quando <= ?")
            args.append(limite)
        if onde:
            sql += " WHERE " + " AND ".join(onde)
        sql += " ORDER BY quando IS NULL, quando, id"
        linhas = [dict(l) for l in c.execute(sql, args).fetchall()]
    return {"ok": True, "tarefas": linhas, "quantidade": len(linhas)}


def tarefa_feita(tarefa, quem=None):
    """Marca como feita. NÃO apaga."""
    with _conn() as c:
        alvo = _achar_tarefa(c, tarefa)
        if not alvo:
            return {"ok": False, "erro": f"'{_norm(tarefa)}' não está nas tarefas pendentes"}
        cheia = c.execute("SELECT * FROM tarefas WHERE id = ?", (alvo["id"],)).fetchone()
        agora = _agora(c)
        c.execute("UPDATE tarefas SET feita = 1, feita_em = ?, feita_por = ? WHERE id = ?",
                  (agora, quem, alvo["id"]))
        r = {"ok": True, "acao": "feita", "id": alvo["id"],
             "tarefa": alvo["tarefa"], "feita_em": agora}
        if cheia["repete"]:
            prox = _proxima_data(c, cheia["quando"], cheia["repete"])
            novo = c.execute(
                "INSERT INTO tarefas (tarefa, quando, de_quem, criado_em, criado_por,"
                " repete, repete_de) VALUES (?,?,?,?,?,?,?)",
                (cheia["tarefa"], prox, cheia["de_quem"], agora, quem,
                 cheia["repete"], cheia["id"])).lastrowid
            r["proxima"] = {"id": novo, "quando": prox, "repete": cheia["repete"]}
    return r


def tarefa_corrigir(tarefa, novo_texto=None, novo_quando=None, quem=None):
    """Muda o texto e/ou a data. Não mexe em feita."""
    if novo_texto is None and novo_quando is None:
        return {"ok": False, "erro": "não disse o que corrigir"}
    with _conn() as c:
        alvo = _achar_tarefa(c, tarefa)
        if not alvo:
            return {"ok": False, "erro": f"'{_norm(tarefa)}' não está nas tarefas pendentes"}
        texto = " ".join(str(novo_texto).strip().split()) if novo_texto else alvo["tarefa"]
        dia = alvo["quando"]
        if novo_quando is not None:
            dia = _data_ok(c, novo_quando) if novo_quando else None
            if novo_quando and not dia:
                return {"ok": False, "erro": f"data inválida: {novo_quando!r}"}
        c.execute("UPDATE tarefas SET tarefa = ?, quando = ? WHERE id = ?",
                  (texto, dia, alvo["id"]))
    return {"ok": True, "acao": "corrigida", "id": alvo["id"],
            "antes": alvo["tarefa"], "agora": texto,
            "quando_antes": alvo["quando"], "quando": dia}


FERRAMENTAS.update({
    "tarefa_add": tarefa_add,
    "tarefas_ver": tarefas_ver,
    "tarefa_feita": tarefa_feita,
    "tarefa_corrigir": tarefa_corrigir,
})


# =========================================================================
# DESFAZER E APOSENTAR (07/09/2026)
# Toda operação de criar precisa da de corrigir junto — foi a lição mais cara
# do dia. Estas três fecham os buracos que sobraram.
# =========================================================================

def desmarcar_pago(nome, competencia=None, quem=None):
    """Desfaz o pagamento de um mês: "na verdade não paguei".

    Aqui a linha é APAGADA, não marcada. É a exceção à regra da casa, e por
    um motivo concreto: UNIQUE(conta_id, competencia) impede que exista duas
    linhas do mesmo mês, então uma linha 'estornada' bloquearia o pagamento
    de verdade quando ele acontecesse. Quem quiser auditoria olha o
    'historico', que guarda a mensagem que pediu isso.
    """
    nome = _norm(nome)
    with _conn() as c:
        conta = c.execute("SELECT id, nome FROM contas WHERE nome = ?", (nome,)).fetchone()
        if not conta:
            return {"ok": False, "erro": f"conta '{nome}' não existe"}
        comp = competencia or competencia_atual(c)
        pag = c.execute("SELECT * FROM pagamentos WHERE conta_id = ? AND competencia = ?",
                        (conta["id"], comp)).fetchone()
        if not pag:
            return {"ok": False, "erro": f"'{conta['nome']}' não estava marcada como paga "
                                         f"em {comp}"}
        c.execute("DELETE FROM pagamentos WHERE id = ?", (pag["id"],))
    return {"ok": True, "acao": "desmarcado", "nome": conta["nome"], "competencia": comp,
            "valor_que_estava": pag["valor_pago"],
            "aviso": "o gasto no banco, se houver, precisa ser estornado à parte"}


def conta_desativar(nome, quem=None):
    """Aposenta uma conta que não se paga mais. Não apaga o histórico dela."""
    nome = _norm(nome)
    with _conn() as c:
        conta = c.execute("SELECT id, nome, ativo FROM contas WHERE nome = ?",
                          (nome,)).fetchone()
        if not conta:
            return {"ok": False, "erro": f"conta '{nome}' não existe"}
        if not conta["ativo"]:
            return {"ok": True, "acao": "ja_estava_desativada", "nome": conta["nome"]}
        c.execute("UPDATE contas SET ativo = 0 WHERE id = ?", (conta["id"],))
        pagos = c.execute("SELECT COUNT(*) FROM pagamentos WHERE conta_id = ?",
                          (conta["id"],)).fetchone()[0]
    return {"ok": True, "acao": "desativada", "nome": conta["nome"],
            "pagamentos_guardados": pagos}


def esquecer_fato(assunto):
    """Apaga um fato que não vale mais (senha velha do wifi, por exemplo)."""
    alvo = _norm(assunto)
    with _conn() as c:
        linhas = c.execute("SELECT id, assunto, valor FROM fatos").fetchall()
        achado = _casar(alvo, [(l["id"], l["assunto"]) for l in linhas])
        if not achado:
            return {"ok": False, "erro": f"não achei fato sobre '{alvo}'"}
        f = next(l for l in linhas if l["id"] == achado[0])
        c.execute("DELETE FROM fatos WHERE id = ?", (f["id"],))
    return {"ok": True, "acao": "esquecido", "assunto": f["assunto"],
            "valor_que_estava": f["valor"]}


FERRAMENTAS.update({
    "desmarcar_pago": desmarcar_pago,
    "conta_desativar": conta_desativar,
    "esquecer_fato": esquecer_fato,
})


def quanto_sobra(competencia=None):
    """O que tem no banco menos as contas fixas ainda não pagas deste mês.

    Junta duas coisas que já existiam separadas: sem isso ninguém soma o saldo
    com o que ainda vai sair.
    """
    with _conn() as c:
        comp = competencia or competencia_atual(c)
        saldo = c.execute(
            "SELECT COALESCE(SUM(valor),0) FROM movimentos WHERE estornado = 0"
        ).fetchone()[0]
        # Água e luz mudam de valor todo mês, então a regra costuma ficar sem
        # valor. Nesse caso o último pagamento é a melhor estimativa — contar
        # como zero faria o "quanto sobra" mentir pra mais.
        faltando = c.execute(
            "SELECT c.id, c.nome, c.valor, c.dia_vencimento,"
            "       (SELECT valor_pago FROM pagamentos u WHERE u.conta_id = c.id"
            "         AND u.valor_pago IS NOT NULL"
            "         ORDER BY u.competencia DESC LIMIT 1) AS ultimo"
            "  FROM contas c"
            "  LEFT JOIN pagamentos p ON p.conta_id = c.id AND p.competencia = ?"
            " WHERE c.ativo = 1 AND p.id IS NULL"
            " ORDER BY c.dia_vencimento", (comp,)).fetchall()

    a_pagar, sem_ideia = [], []
    for l in faltando:
        valor, origem = l["valor"], "regra"
        if not valor:
            valor, origem = l["ultimo"], "último pagamento"
        if not valor:
            sem_ideia.append(l["nome"])
            continue
        a_pagar.append({"nome": l["nome"], "valor": round(valor, 2),
                        "dia_vencimento": l["dia_vencimento"], "valor_de": origem})

    total = sum(x["valor"] for x in a_pagar)
    r = {"ok": True, "competencia": comp, "saldo": round(saldo, 2),
         "contas_a_pagar": a_pagar, "total_a_pagar": round(total, 2),
         "sobra": round(saldo - total, 2)}
    if sem_ideia:
        r["sem_valor_conhecido"] = sem_ideia
        r["aviso"] = ("estas contas não têm valor conhecido e ficaram FORA da conta: "
                      + ", ".join(sem_ideia))
    return r


FERRAMENTAS["quanto_sobra"] = quanto_sobra


def compra_parcelada(descricao, parcelas, valor_parcela=None, valor_total=None,
                     banco=None, categoria=None, quando=None, quem=None):
    """"Geladeira em 10x de 300" — 10 lançamentos, um por mês.

    As parcelas futuras já ficam gravadas, mas só entram no saldo quando o mês
    chega. Assim o extrato de dezembro já sabe da parcela de dezembro, e o
    saldo de hoje não leva um tombo de 3.000 que não aconteceu.
    """
    try:
        n = int(parcelas)
    except (TypeError, ValueError):
        return {"ok": False, "erro": f"número de parcelas inválido: {parcelas!r}"}
    if not 2 <= n <= 60:
        return {"ok": False, "erro": "parcelas tem que ser entre 2 e 60"}

    if valor_parcela is not None:
        try:
            cada = abs(float(valor_parcela))
        except (TypeError, ValueError):
            return {"ok": False, "erro": f"valor inválido: {valor_parcela!r}"}
        total = round(cada * n, 2)
    elif valor_total is not None:
        try:
            total = abs(float(valor_total))
        except (TypeError, ValueError):
            return {"ok": False, "erro": f"valor inválido: {valor_total!r}"}
        cada = round(total / n, 2)
    else:
        return {"ok": False, "erro": "diga o valor da parcela ou o valor total"}
    if not cada:
        return {"ok": False, "erro": "valor zero"}

    descricao = " ".join(str(descricao or "").strip().split())
    if not descricao:
        return {"ok": False, "erro": "diga o que foi comprado"}

    with _conn() as c:
        b, erro = _resolver_banco(c, banco, quem)
        if erro:
            return {"ok": False, "erro": erro}
        dia = (quando or "").strip() or _hoje(c)
        dia = c.execute("SELECT date(?)", (dia,)).fetchone()[0]
        if not dia:
            return {"ok": False, "erro": f"data inválida: {quando!r}"}

        cat = _categoria(categoria)
        # id aleatório: epoch em segundos colidia quando duas compras eram
        # feitas no mesmo segundo, e cancelar uma levava junto a outra
        grupo = uuid.uuid4().hex[:12]
        agora = _agora(c)
        ids = []
        for i in range(n):
            venc = c.execute("SELECT date(?, ?)", (dia, f"+{i} months")).fetchone()[0]
            # a última parcela absorve a sobra do arredondamento
            v = cada if i < n - 1 else round(total - cada * (n - 1), 2)
            ids.append(c.execute(
                "INSERT INTO movimentos (banco_id, valor, descricao, categoria, quando,"
                " competencia, registrado_em, registrado_por, parcela, parcelas, grupo)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (b["id"], -v, f"{descricao} ({i+1}/{n})", cat, venc, venc[:7],
                 agora, quem, i + 1, n, grupo)).lastrowid)
        saldo = _saldo(c, b["id"])

    return {"ok": True, "acao": "parcelado", "grupo": grupo, "ids": ids,
            "descricao": descricao, "banco": b["nome"], "categoria": cat,
            "parcelas": n, "valor_parcela": cada, "valor_total": round(total, 2),
            "primeira": dia, "saldo": round(saldo, 2)}



def parcelas_cancelar(descricao):
    """Cancela as parcelas que ainda não venceram. As já pagas ficam."""
    alvo = _norm(descricao)
    with _conn() as c:
        grupos = c.execute(
            "SELECT DISTINCT grupo, descricao FROM movimentos"
            " WHERE grupo IS NOT NULL AND estornado = 0").fetchall()
        achado = _casar(alvo, [(g["grupo"], g["descricao"]) for g in grupos])
        if not achado:
            return {"ok": False, "erro": f"não achei compra parcelada '{alvo}'"}
        grupo = achado[0]
        futuras = c.execute(
            "SELECT id, parcela, parcelas, valor, quando FROM movimentos"
            " WHERE grupo = ? AND estornado = 0 AND quando > date('now','localtime')"
            " ORDER BY parcela", (grupo,)).fetchall()
        if not futuras:
            return {"ok": False, "erro": "não há parcelas futuras pra cancelar"}
        c.execute("UPDATE movimentos SET estornado = 1 WHERE grupo = ?"
                  "   AND estornado = 0 AND quando > date('now','localtime')", (grupo,))
    return {"ok": True, "acao": "parcelas_canceladas", "grupo": grupo,
            "canceladas": len(futuras),
            "valor_cancelado": round(sum(abs(f["valor"]) for f in futuras), 2),
            "da_parcela": futuras[0]["parcela"], "de": futuras[0]["parcelas"]}


def historico_ver(dias=1, limite=20):
    """O que a Lauren registrou — pra vocês me auditarem sem precisar de SQL."""
    try:
        dias = max(1, int(dias))
    except (TypeError, ValueError):
        dias = 1
    with _conn() as c:
        linhas = [dict(l) for l in c.execute(
            "SELECT ts, quem, mensagem, resposta, ferramentas FROM historico"
            " WHERE ts >= datetime('now','localtime',?) AND ferramentas <> '[]'"
            " ORDER BY id DESC LIMIT ?", (f"-{dias} days", int(limite))).fetchall()]
    for l in linhas:
        try:
            l["ferramentas"] = json.loads(l["ferramentas"] or "[]")
        except (TypeError, ValueError):
            l["ferramentas"] = []
    return {"ok": True, "dias": dias, "registros": linhas, "quantidade": len(linhas)}


FERRAMENTAS.update({
    "compra_parcelada": compra_parcelada,
    "parcelas_cancelar": parcelas_cancelar,
    "historico_ver": historico_ver,
})


def gastos_periodo(desde, ate=None, categoria=None, banco=None):
    """Gasto por categoria entre duas datas — atravessa meses.

    O resumo olha um mês e o comparar_com olha dois. Esta responde "quanto
    gastei com mercado nos últimos 3 meses?".
    """
    with _conn() as c:
        d = c.execute("SELECT date(?)", (str(desde).strip(),)).fetchone()[0]
        if not d:
            return {"ok": False, "erro": f"data inicial inválida: {desde!r}"}
        a = c.execute("SELECT date(?)", (str(ate).strip(),)).fetchone()[0] if ate else _hoje(c)
        if not a:
            return {"ok": False, "erro": f"data final inválida: {ate!r}"}
        if d > a:
            d, a = a, d

        sql = ("SELECT m.categoria, SUM(-m.valor) AS gasto, COUNT(*) AS n"
               "  FROM movimentos m JOIN bancos b ON b.id = m.banco_id"
               " WHERE m.estornado = 0 AND m.valor < 0 AND m.quando BETWEEN ? AND ?")
        args = [d, a]
        if categoria:
            sql += " AND m.categoria = ?"; args.append(_categoria(categoria))
        if banco:
            sql += " AND b.nome = ?"; args.append(_norm(banco))
        sql += " GROUP BY m.categoria ORDER BY gasto DESC"
        cats = [{"categoria": l["categoria"], "gasto": round(l["gasto"], 2),
                 "lancamentos": l["n"]} for l in c.execute(sql, args).fetchall()]
        meses = c.execute("SELECT COUNT(DISTINCT competencia) FROM movimentos"
                          " WHERE estornado = 0 AND quando BETWEEN ? AND ?",
                          (d, a)).fetchone()[0] or 1

    total = sum(x["gasto"] for x in cats)
    return {"ok": True, "desde": d, "ate": a, "categorias": cats,
            "total_gasto": round(total, 2), "meses": meses,
            "media_por_mes": round(total / meses, 2)}


FERRAMENTAS["gastos_periodo"] = gastos_periodo
