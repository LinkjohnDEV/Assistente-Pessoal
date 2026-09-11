# Passo 9 aprovado em 07/09/2026: roteiro da seção 12 rodado inteiro contra a
# API real (lauren-4), com o ciclo de tool calling fechando e a gravação
# conferida por SQL a cada passo. Este arquivo está verificado.

"""A camada da IA. É o ÚNICO arquivo que sabe qual modelo está atrás disso.

Fala o padrão chat completions (https://ialauren.com/docs/api) usando só a
biblioteca padrão — sem venv, sem pip, sem o pacote openai. Trocar a Lauren por
outro provedor do mesmo padrão é mudar base_url e api_key no config.json.

O que este arquivo NÃO faz: lembrar das coisas. Quem lembra é o banco, através
de ferramentas.py. Aqui só se traduz português em chamada de função.
"""

import json
import os
import time
import urllib.error
import urllib.request

import ferramentas

AQUI = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(AQUI, "config.json")

TETO_VOLTAS = 20      # agente sem teto é conta aberta (seção 4 da API)
TIMEOUT = 120
PLACEHOLDERS = ("COLAR-AQUI", "DEFINIR", "SEU-ENDPOINT-DA-LAUREN",
                "GERAR-COM-openssl-rand-base64-32", "CHAVE-DO-CANAL",
                "TOKEN-DO-CANAL", "1203634XXXXXXXXX@g.us")


class ErroLauren(Exception):
    """Erro vindo da API, já com mensagem pronta pra mostrar pra gente."""


# ------------------------------------------------------------------ config

def carregar_config(exigir=("lauren",)):
    """Lê config.json e RECUSA rodar com placeholder não preenchido."""
    if not os.path.exists(CONFIG):
        raise ErroLauren(f"{CONFIG} não existe. Copie o modelo da seção 7 do documento.")
    with open(CONFIG) as f:
        cfg = json.load(f)

    pendentes = []
    for secao in exigir:
        for chave, valor in cfg.get(secao, {}).items():
            if isinstance(valor, str) and any(p in valor for p in PLACEHOLDERS):
                pendentes.append(f"{secao}.{chave} = {valor!r}")
    if pendentes:
        raise ErroLauren(
            "config.json ainda tem placeholder:\n  " + "\n  ".join(pendentes) +
            "\nPreencha antes de rodar. Nada aqui inventa valor."
        )

    # a chave pode vir do ambiente, como manda a lista de conferência da API
    if os.environ.get("LAUREN_API_KEY"):
        cfg.setdefault("lauren", {})["api_key"] = os.environ["LAUREN_API_KEY"]
    return cfg


# ------------------------------------------------------- as 8 ferramentas
# O parâmetro `quem` NÃO aparece aqui de propósito: quem falou vem do JID da
# mensagem, não da opinião do modelo. Ele não pode dizer que foi a esposa.

ESQUEMA_FERRAMENTAS = [
    {"type": "function", "function": {
        "name": "salvar_conta",
        "description": "Cria/atualiza a REGRA de conta que se repete (aluguel, luz). Não paga.",
        "parameters": {"type": "object", "properties": {
            "nome": {"type": "string", "description": "Nome curto da conta, ex: 'aluguel'"},
            "dia": {"type": "integer", "description": "Dia do vencimento, 1 a 31"},
            "valor": {"type": "number", "description": "Valor em reais. 2300 é dois mil e trezentos."},
        }, "required": ["nome", "dia", "valor"]}}},

    {"type": "function", "function": {
        "name": "marcar_pago",
        "description": "Registra o pagamento de uma conta num mês. Sem competencia, o atual.",
        "parameters": {"type": "object", "properties": {
            "nome": {"type": "string"},
            "competencia": {"type": "string", "description": "Mês no formato AAAA-MM. Omita para o mês atual."},
            "valor_pago": {"type": "number", "description": "Só se foi diferente do valor da conta."},
            "banco": {"type": "string", "description": "De onde saiu o dinheiro. Passe "
                                                        "sempre que disserem — assim o saldo também cai."},
        }, "required": ["nome"]}}},

    {"type": "function", "function": {
        "name": "consultar_contas",
        "description": "Contas com vencimento, valor, dias restantes e se o mês já foi pago. "
                       "Use sempre que perguntarem de conta — nunca responda de memória.",
        "parameters": {"type": "object", "properties": {
            "nome": {"type": "string", "description": "Omita para listar todas."},
        }, "required": []}}},

    {"type": "function", "function": {
        "name": "lista_add",
        "description": "Põe um item na lista de compras. Um item por chamada.",
        "parameters": {"type": "object", "properties": {
            "item": {"type": "string"},
            "onde": {"type": "string", "description": "Onde se compra: mercado, casa, "
                                                       "farmácia. Só se der pra saber."},
            "valor": {"type": "number", "description": "Preço estimado, se disserem. "
                                                        "OPCIONAL — item sem preço entra igual."},
        }, "required": ["item"]}}},

    {"type": "function", "function": {
        "name": "lista_marcar_comprado",
        "description": "Marca item da lista como comprado.",
        "parameters": {"type": "object", "properties": {
            "item": {"type": "string"},
        }, "required": ["item"]}}},

    {"type": "function", "function": {
        "name": "lista_ver",
        "description": "O que falta comprar, com o total do que tem preço. Use sempre "
                       "que perguntarem da lista.",
        "parameters": {"type": "object", "properties": {
            "onde": {"type": "string", "description": "Filtra por contexto: 'o que falta "
                                                       "pro mercado?' → onde=mercado"},
        }, "required": []}}},

    {"type": "function", "function": {
        "name": "lembrar_fato",
        "description": "Guarda um fato: senha do wifi, aniversário. Mesmo assunto atualiza.",
        "parameters": {"type": "object", "properties": {
            "assunto": {"type": "string", "description": "Chave curta, ex: 'wifi'"},
            "valor": {"type": "string", "description": "O conteúdo do fato"},
        }, "required": ["assunto", "valor"]}}},

    {"type": "function", "function": {
        "name": "buscar_fato",
        "description": "Procura nos fatos guardados. Use antes de dizer que não sabe.",
        "parameters": {"type": "object", "properties": {
            "termo": {"type": "string"},
        }, "required": ["termo"]}}},
    {"type": "function", "function": {
        "name": "lista_corrigir",
        "description": "Muda nome/contexto/preço de item. NUNCA use lista_marcar_comprado "
                       "pra consertar nome.",
        "parameters": {"type": "object", "properties": {
            "item": {"type": "string", "description": "Como está hoje"},
            "novo_nome": {"type": "string", "description": "Como deve ficar"},
            "onde": {"type": "string", "description": "Muda o contexto"},
            "valor": {"type": "number", "description": "Muda o preço estimado"},
        }, "required": ["item"]}}},

    {"type": "function", "function": {
        "name": "banco_salvar",
        "description": "Cadastra banco/cartão/dinheiro. Use quando disserem quanto têm.",
        "parameters": {"type": "object", "properties": {
            "nome": {"type": "string", "description": "Ex: 'inter', 'nubank'"},
            "tipo": {"type": "string", "enum": ["conta", "cartao", "dinheiro"]},
            "saldo_inicial": {"type": "number", "description": "Quanto tem hoje, se disserem"},
        }, "required": ["nome"]}}},

    {"type": "function", "function": {
        "name": "gasto_registrar",
        "description": "Dinheiro que SAIU. 'uber 27' = valor 27, descrição uber.",
        "parameters": {"type": "object", "properties": {
            "valor": {"type": "number", "description": "Sempre positivo"},
            "descricao": {"type": "string"},
            "banco": {"type": "string"},
            "categoria": {"type": "string", "enum": ferramentas.CATEGORIAS},
            "quando": {"type": "string", "description": "AAAA-MM-DD, só se não for hoje"},
        }, "required": ["valor"]}}},

    {"type": "function", "function": {
        "name": "entrada_registrar",
        "description": "Registra dinheiro que ENTROU: salário, pix recebido, devolução.",
        "parameters": {"type": "object", "properties": {
            "valor": {"type": "number", "description": "Sempre positivo"},
            "descricao": {"type": "string"},
            "banco": {"type": "string"},
            "quando": {"type": "string", "description": "AAAA-MM-DD, só se não for hoje"},
        }, "required": ["valor"]}}},

    {"type": "function", "function": {
        "name": "saldo_ver",
        "description": "Quanto tem. Sem nome, todos os bancos. Nunca calcule saldo de cabeça.",
        "parameters": {"type": "object", "properties": {
            "banco": {"type": "string"},
        }, "required": []}}},

    {"type": "function", "function": {
        "name": "extrato",
        "description": "Lançamentos do mês, um a um.",
        "parameters": {"type": "object", "properties": {
            "banco": {"type": "string"},
            "competencia": {"type": "string", "description": "AAAA-MM. Omita para o mês atual."},
            "categoria": {"type": "string", "enum": ferramentas.CATEGORIAS},
        }, "required": []}}},

    {"type": "function", "function": {
        "name": "resumo",
        "description": "Gasto por categoria no MÊS, com limite. Pra 'quanto gastei esse mês'.",
        "parameters": {"type": "object", "properties": {
            "competencia": {"type": "string", "description": "AAAA-MM. Omita para o mês atual."},
            "comparar_com": {"type": "string", "description": "AAAA-MM de outro mês, pra "
                                                              "'gastei mais que mês passado?'"},
        }, "required": []}}},

    {"type": "function", "function": {
        "name": "gastos_periodo",
        "description": "Gasto entre duas datas, atravessando meses. Pra 'últimos 3 meses'. "
                       "Para UM mês use resumo.",
        "parameters": {"type": "object", "properties": {
            "desde": {"type": "string", "description": "AAAA-MM-DD. Calcule a partir de hoje."},
            "ate": {"type": "string", "description": "AAAA-MM-DD. Omita para hoje."},
            "categoria": {"type": "string", "enum": ferramentas.CATEGORIAS},
            "banco": {"type": "string"},
        }, "required": ["desde"]}}},

    {"type": "function", "function": {
        "name": "quanto_sobra",
        "description": "Saldo menos as contas fixas não pagas. Pra 'quanto sobra'.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},

    {"type": "function", "function": {
        "name": "estornar",
        "description": "Desfaz um lançamento pelo número (#) que aparece na confirmação.",
        "parameters": {"type": "object", "properties": {
            "id": {"type": "integer", "description": "O número do lançamento"},
        }, "required": ["id"]}}},

    {"type": "function", "function": {
        "name": "limite_definir",
        "description": "Teto de gasto de uma categoria por mês.",
        "parameters": {"type": "object", "properties": {
            "categoria": {"type": "string", "enum": ferramentas.CATEGORIAS},
            "valor_mes": {"type": "number"},
        }, "required": ["categoria", "valor_mes"]}}},
    {"type": "function", "function": {
        "name": "tarefa_add",
        "description": "Algo a FAZER. Com data entra no aviso diário; sem data é só pendência.",
        "parameters": {"type": "object", "properties": {
            "tarefa": {"type": "string", "description": "O que precisa ser feito"},
            "quando": {"type": "string", "description": "AAAA-MM-DD. Converta 'quinta', "
                                                        "'amanhã', 'outubro' a partir da data de hoje. Omita se não houver prazo."},
            "de_quem": {"type": "string", "description": "De quem é a tarefa, se disserem"},
            "repete": {"type": "string", "enum": ["diaria", "semanal", "mensal"],
                       "description": "Só se for coisa que se repete: 'toda segunda', "
                                      "'todo dia 5', 'todo dia'. Quando marcada feita, "
                                      "a próxima nasce sozinha."},
        }, "required": ["tarefa"]}}},

    {"type": "function", "function": {
        "name": "tarefas_ver",
        "description": "O que falta fazer, com dias restantes (negativo = atrasada).",
        "parameters": {"type": "object", "properties": {
            "incluir_feitas": {"type": "boolean", "description": "true pra ver o que já foi feito"},
            "ate": {"type": "string", "description": "AAAA-MM-DD: só o que vence até essa data"},
        }, "required": []}}},

    {"type": "function", "function": {
        "name": "tarefa_feita",
        "description": "Marca uma tarefa como feita. Use quando disserem que "
                       "fizeram, concluíram, terminaram ou resolveram algo — "
                       "mesmo que a frase tenha a palavra 'conta' dentro. "
                       "SE O RESULTADO TRAZ 'proxima', mostre essa data na sua "
                       "resposta: é uma tarefa que se repete e a pessoa precisa "
                       "saber que a próxima já está agendada.",
        "parameters": {"type": "object", "properties": {
            "tarefa": {"type": "string"},
        }, "required": ["tarefa"]}}},

    {"type": "function", "function": {
        "name": "tarefa_corrigir",
        "description": "Muda texto e/ou data. NUNCA use tarefa_feita pra consertar texto.",
        "parameters": {"type": "object", "properties": {
            "tarefa": {"type": "string", "description": "Como está hoje"},
            "novo_texto": {"type": "string"},
            "novo_quando": {"type": "string", "description": "AAAA-MM-DD, ou vazio pra tirar o prazo"},
        }, "required": ["tarefa"]}}},
    {"type": "function", "function": {
        "name": "desmarcar_pago",
        "description": "Desfaz o pagamento de um mês. Não estorna o gasto no banco.",
        "parameters": {"type": "object", "properties": {
            "nome": {"type": "string"},
            "competencia": {"type": "string", "description": "AAAA-MM. Omita para o mês atual."},
        }, "required": ["nome"]}}},

    {"type": "function", "function": {
        "name": "conta_desativar",
        "description": "Aposenta conta que não se paga mais. O histórico fica.",
        "parameters": {"type": "object", "properties": {
            "nome": {"type": "string"},
        }, "required": ["nome"]}}},

    {"type": "function", "function": {
        "name": "esquecer_fato",
        "description": "Apaga um fato que não vale mais (senha velha, por exemplo).",
        "parameters": {"type": "object", "properties": {
            "assunto": {"type": "string"},
        }, "required": ["assunto"]}}},
    {"type": "function", "function": {
        "name": "compra_parcelada",
        "description": "Compra em vezes: '10x de 300'. Um lançamento por mês; as futuras "
                       "não mexem no saldo de hoje.",
        "parameters": {"type": "object", "properties": {
            "descricao": {"type": "string", "description": "O que foi comprado"},
            "parcelas": {"type": "integer", "description": "Em quantas vezes"},
            "valor_parcela": {"type": "number", "description": "Quanto cada uma, se "
                                                               "disserem '10x de 300'"},
            "valor_total": {"type": "number", "description": "O total, se disserem "
                                                             "'3000 em 10x'. Com entrada, é o preço CHEIO."},
            "entrada": {"type": "number", "description": "Pago à vista na hora: 'dei 1000 "
                                                          "de entrada e 5x de 500'. Vira "
                                                          "lançamento separado, hoje."},
            "banco": {"type": "string"},
            "categoria": {"type": "string", "enum": ferramentas.CATEGORIAS},
            "quando": {"type": "string", "description": "AAAA-MM-DD da PRIMEIRA PARCELA. "
                                                        "'todo dia 20' = o próximo dia 20."},
        }, "required": ["descricao", "parcelas"]}}},

    {"type": "function", "function": {
        "name": "parcelas_cancelar",
        "description": "Cancela as parcelas que ainda NÃO venceram. As já pagas ficam.",
        "parameters": {"type": "object", "properties": {
            "descricao": {"type": "string", "description": "O que foi comprado"},
        }, "required": ["descricao"]}}},

    {"type": "function", "function": {
        "name": "historico_ver",
        "description": "O que VOCÊ registrou nos últimos dias. Pra 'o que você anotou hoje'.",
        "parameters": {"type": "object", "properties": {
            "dias": {"type": "integer", "description": "Quantos dias pra trás. 1 = hoje."},
        }, "required": []}}},
]


INSTRUCOES = """Você é a Lauren, assistente de um casal, num grupo de WhatsApp da casa deles.
Cuida de três coisas: contas a pagar, lista de compras e fatos soltos da casa.

ANTES DE MAIS NADA — "CONTA" TEM DOIS SENTIDOS

O que decide NÃO é a palavra "conta", é o VERBO da frase:

  "fiz" / "concluí" / "terminei" / "resolvi" / "já foi" / "acabei"
      → é TAREFA. Chame tarefas_ver e depois tarefa_feita.
  "paguei" / "vence" / "já foi paga" / "quanto devo"
      → é CONTA A PAGAR. Chame consultar_contas ou marcar_pago.

"Foi concluída a troca da conta de água e luz" é TAREFA. A palavra "conta"
está ali porque é o texto da tarefa, não porque é conta a pagar.

Se VOCÊ acabou de perguntar qual tarefa foi feita, a próxima mensagem que
nomear uma tarefa é a RESPOSTA da sua pergunta — marque como feita. Não crie
outra igual.

NUNCA responda "não achei" sobre algo que disseram ter concluído sem ter
chamado tarefas_ver antes. Se não achar num lugar, olhe no outro — as duas
buscas são baratas.

A SUA MEMÓRIA É O BANCO, NÃO A CONVERSA.
Nunca responda de cabeça sobre conta, vencimento, pagamento, lista ou fato guardado.
Chame a ferramenta e responda com o que ela devolveu. Se não chamou, você não sabe.

NUNCA diga que anotou, marcou ou guardou sem ter chamado a ferramenta e recebido
"ok": true. Dizer "marquei!" sem gravar é o pior erro possível aqui.

NÃO afirme nada da casa que a ferramenta não tenha devolvido no resultado. Se a
informação não veio no retorno, diga que não achou no banco — não complete com o
que você lembra da conversa. O que está no histórico não é prova; o banco é.

Se a ferramenta devolver "ok": false, diga o que deu errado, sem enfeitar.

COMO ESCREVER A RESPOSTA

WhatsApp, tela estreita: texto longo quebra sozinho. *Negrito* com um
asterisco de cada lado. Nada de **, # ou tabela.

1. UM item = recibo, uma informação por linha. VÁRIOS = uma linha por item,
   com o que distingue na MESMA linha, após travessão. Nunca quebre um item em
   duas linhas dentro de uma lista — vira um monte de linha sem começo nem fim.
2. Linha em branco entre o título e a lista, e entre blocos.
3. Data igual pra todos: diga uma vez no título, não repita em cada linha.
4. Não mostre quem pediu, comprou ou gravou, a não ser que perguntem.
5. Nunca invente linha que a ferramenta não devolveu. Melhor três certas que
   seis bonitas. Nunca some nem converta valor de cabeça.

UM item (recibo):
✅ *Gasto registrado!*
🛒 Chuveiro
🏷️ Moradia
💸 R$ 50,00
🏦 Inter — saldo: R$ 1.150,00
🔢 #12

VÁRIOS (uma linha cada):
📌 *Suas tarefas*

🔴 Pagar o IPVA — atrasada há 2 dias
📅 Revisar o carro — 12/10
⚪ Comprar presente da vó — sem prazo

Sempre mostre o número (#) do lançamento — é por ele que se estorna.
Entrada usa ⬆️, nunca 💸 (que é saída).

Erro ou nada encontrado: diga o CAMINHO, não deixe a pessoa no vazio.
⚠️ Nenhum banco cadastrado ainda.
Me diz quanto tem e onde: "Lauren, tenho 1200 no Inter"

NUNCA comece a SUA mensagem com "Lauren," — é o seu nome, você é quem fala.
Ele só aparece no exemplo que você dá pra pessoa responder: o grupo só te
entrega mensagem que começa com "Lauren", então quem responder só "inter" não
chega até você. Ao perguntar algo, mostre a resposta pronta:

De qual banco foi? Responda assim: *Lauren, foi do Inter*

Conversa solta, sem nada do banco: uma frase simples, sem emoji e sem formato.

DINHEIRO

"uber 27" é gasto de R$ 27,00. Valor sempre positivo — o sinal é da ferramenta.
Categoria da lista; na dúvida, Outros. Vários valores = vários lançamentos.

banco_salvar só CRIA banco novo; nunca mexe em saldo. Saldo muda por
gasto_registrar e entrada_registrar. Se não der pra saber se o dinheiro ENTROU
ou SAIU, PERGUNTE antes de gravar — com dinheiro não se chuta.

Pagaram conta E disseram de onde? Passe o banco no marcar_pago: registra e o
saldo cai junto. Não disseram? Registre assim mesmo e avise numa linha que o
saldo não mudou — não fique perguntando.

"Em 10x", "parcelado em 12" é compra_parcelada. O saldo cai só a primeira; as
outras entram no mês delas.

Entrada + parcelas ("paguei 1000 e vou pagar 5x de 500") é UMA chamada só, com
entrada=1000, parcelas=5, valor_parcela=500. Não transforme a entrada em
parcela — o total ficaria errado. "Todo dia 20" é a data da primeira parcela.

Comprou algo que está na lista E disse o valor: marque na lista E lance o gasto.

TAREFAS

Coisa a FAZER com data vai em tarefa_add, não em lembrar_fato: tarefa entra no
aviso das 6h, fato só fica guardado e nunca avisa. Converta "quinta", "amanhã",
"dia 12" pra AAAA-MM-DD usando a data de hoje.

"Toda segunda", "todo dia 5", "todo dia" é tarefa que SE REPETE: passe
repete=semanal|mensal|diaria. Ao marcá-la feita a ferramenta devolve "proxima"
— MOSTRE essa data, senão a pessoa acha que sumiu e cadastra de novo.

✅ *Feito!*
📌 Levar o lixo pra rua
🔁 Próxima: 16/09

LISTA DE COMPRAS

`onde` separa mercado de casa; `valor` é preço estimado. Os dois são OPCIONAIS
— item sem eles entra igual. Só mostre total se houver preço, e diga quantos
itens entraram nele:

🛒 *Falta pra casa* — 3 itens, uns R$ 3.250 (2 de 3 com preço)

• guarda-roupa casal — ~R$ 1.200
• armário cozinha — ~R$ 1.800
• chuveiro

DESFAZER

desmarcar_pago desfaz pagamento · estornar desfaz lançamento ·
parcelas_cancelar mata as parcelas futuras · tarefa_corrigir e lista_corrigir
arrumam texto, contexto e preço · conta_desativar aposenta conta ·
esquecer_fato apaga fato vencido.

NUNCA use a ferramenta de CONCLUIR (marcar comprado, marcar feita, marcar
pago) pra consertar erro — só quando a coisa aconteceu de verdade.

Hoje é {hoje}. A competência do mês atual é {competencia}."""


class Cerebro:
    def __init__(self, config):
        lauren = config["lauren"]
        self.url = lauren["base_url"].rstrip("/") + "/chat/completions"
        self.chave = lauren["api_key"]
        self.modelo = lauren.get("modelo") or "lauren-4"

    # -------------------------------------------------------- rede
    def _chamar(self, mensagens):
        corpo = json.dumps({
            "model": self.modelo,
            "messages": mensagens,
            "tools": ESQUEMA_FERRAMENTAS,
            "tool_choice": "auto",
        }, ensure_ascii=False).encode("utf-8")

        pedido = urllib.request.Request(self.url, data=corpo, headers={
            "Authorization": f"Bearer {self.chave}",
            "Content-Type": "application/json",
        })

        ultima = None
        for tentativa in range(3):
            try:
                with urllib.request.urlopen(pedido, timeout=TIMEOUT) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                bruto = e.read().decode("utf-8", "replace")
                try:
                    msg = json.loads(bruto)["error"]["message"]
                except Exception:
                    msg = bruto.strip()[:400] or e.reason

                if e.code in (401, 403):
                    err = ErroLauren(f"[{e.code}] {msg}")
                    err.status = e.code   # conta/chave: tentar de novo não resolve
                    raise err from None
                if e.code == 429:
                    # a mensagem já vem escrita pra gente ler: mostrar inteira
                    err = ErroLauren(f"Cota estourada: {msg}")
                    err.status = 429
                    raise err from None
                if e.code in (502, 503) and tentativa < 2:
                    ultima = f"[{e.code}] {msg}"
                    time.sleep(2 ** tentativa)      # recuo exponencial
                    continue
                raise ErroLauren(f"[{e.code}] {msg}") from None
            except urllib.error.URLError as e:
                if tentativa < 2:
                    ultima = f"rede: {e.reason}"
                    time.sleep(2 ** tentativa)
                    continue
                raise ErroLauren(f"Não consegui falar com a Lauren: {e.reason}") from None
        raise ErroLauren(f"A Lauren não respondeu depois de 3 tentativas. Último: {ultima}")

    # -------------------------------------------------------- o laço
    def responder(self, mensagem, quem=None, historico=None):
        """Devolve dict: resposta, ferramentas, tokens_in, tokens_out, modelo.

        `historico` é a lista de turnos anteriores (sem o system). A rota não
        guarda nada — o histórico inteiro vai junto em toda chamada.
        """
        agora = ferramentas.competencia_atual()
        hoje = time.strftime("%d/%m/%Y")
        sistema = INSTRUCOES.format(hoje=hoje, competencia=agora)

        mensagens = [{"role": "system", "content": sistema}]
        mensagens += _limpar(historico or [])
        mensagens.append({"role": "user", "content": mensagem})

        usadas, tin, tout, modelo_real = [], 0, 0, self.modelo

        for _ in range(TETO_VOLTAS):
            try:
                bruto = self._chamar(mensagens)
            except ErroLauren as e:
                # quem for reprocessar precisa saber: se já gravou, repetir grava 2x
                e.ferramentas_ja_rodadas = list(usadas)
                raise
            uso = bruto.get("usage") or {}
            tin += uso.get("prompt_tokens", 0)
            tout += uso.get("completion_tokens", 0)
            modelo_real = bruto.get("model", modelo_real)   # o da RESPOSTA

            msg = bruto["choices"][0]["message"]
            mensagens.append(msg)                            # o turno INTEIRO volta

            chamadas = msg.get("tool_calls")
            if not chamadas:
                texto = (msg.get("content") or "").strip()
                if not texto:
                    # turno vazio envenena o histórico: não guardar, não devolver
                    mensagens.pop()
                    texto = "Não consegui formular a resposta. Repete?"
                return {"resposta": texto, "ferramentas": usadas,
                        "tokens_in": tin, "tokens_out": tout,
                        "modelo": modelo_real, "mensagens": mensagens[1:]}

            for chamada in chamadas:
                nome = chamada["function"]["name"]
                try:
                    args = json.loads(chamada["function"]["arguments"] or "{}")  # STRING JSON
                except json.JSONDecodeError as e:
                    resultado = {"ok": False, "erro": f"argumentos ilegíveis: {e}"}
                else:
                    funcao = ferramentas.FERRAMENTAS.get(nome)
                    if funcao is None:
                        resultado = {"ok": False, "erro": f"ferramenta '{nome}' não existe"}
                    else:
                        if quem is not None and nome != "consultar_contas" \
                                and "quem" in funcao.__code__.co_varnames:
                            args["quem"] = quem      # quem falou vem do JID, não do modelo
                        try:
                            resultado = funcao(**args)
                        except TypeError as e:
                            resultado = {"ok": False, "erro": f"argumentos errados: {e}"}
                        except Exception as e:
                            resultado = {"ok": False, "erro": f"{type(e).__name__}: {e}"}

                usadas.append({"nome": nome, "args": args, "resultado": resultado})
                mensagens.append({
                    "role": "tool",
                    "tool_call_id": chamada["id"],
                    "content": json.dumps(resultado, ensure_ascii=False, default=str),
                })

        return {"resposta": "Me embananei toda nessa. Pergunta de novo, mais simples?",
                "ferramentas": usadas, "tokens_in": tin, "tokens_out": tout,
                "modelo": modelo_real, "mensagens": mensagens[1:]}


def _limpar(historico):
    """Tira turnos de assistente vazios — reenviá-los trava a conversa."""
    limpo = []
    for m in historico:
        if m.get("role") == "assistant" and not m.get("tool_calls") \
                and not (m.get("content") or "").strip():
            continue
        limpo.append(m)
    return limpo
