# Assistente de Casa — especificação da máquina

Documento de montagem. Máquina dedicada, isolada do painel StarCore.
Cérebro = API da Lauren. Interface = grupo de WhatsApp via HuberChat.

---

## ⛔ LEIA ISTO PRIMEIRO — PARE NO PASSO 6

Este documento descreve o projeto inteiro, mas **só os passos 1 a 6 do
checklist (seção 11) devem ser executados agora.**

Do passo 7 em diante depende da **API da Lauren, que ainda não existe** — está
sendo construída em paralelo, em outra máquina. Sem ela, não há cérebro.

**O que NÃO fazer, em nenhuma hipótese:**

- ❌ Escrever `cerebro.py` "provisório"
- ❌ Criar um mock, stub ou fallback que devolva resposta pronta
- ❌ Chamar direto a OpenAI ou qualquer outro modelo "só pra testar"
- ❌ Preencher `config.json` com valores inventados
- ❌ Marcar os testes da seção 12 como aprovados sem um cérebro real

**Por quê:** um cérebro falso faz os testes passarem. Aí o relatório diz
"funcionando" e o que existe é um boneco — a mesma falha que este documento
descreve na seção 6, só que auto-infligida. É pior do que não ter feito, porque
esconde o problema em vez de mostrar.

**Entregue os passos 1 a 6 e pare.** Ao terminar, relate: o que foi criado, o
resultado do teste do passo 6, e o que ficou pendente. O dono decide o resto.

Os passos 1 a 6 são a parte que mais importa e **não precisam de IA nenhuma**:
o banco e as 8 funções puras. Se isso estiver certo, o resto é um arquivo.

---

## 1. O que é isso

Um assistente que vive num grupo de WhatsApp (você + esposa) e **lembra das
coisas da casa**: contas a pagar, o que falta comprar, fatos soltos.

```
Você:   Lauren, meu aluguel é dia 23 todo mês, valor 2300
Lauren: Anotei: aluguel, todo dia 23, R$ 2300.

(duas semanas depois)

Você:   Lauren, meu aluguel vence quando?
Lauren: Dia 23 — daqui a 4 dias. R$ 2300. Não tá marcado como pago.

Você:   Lauren, o que falta comprar pra casa?
Lauren: Cabeça de chuveiro, sabão.

Você:   Lauren já comprei sabão
Lauren: Marquei. Falta só a cabeça de chuveiro.
```

### A regra que faz isso funcionar

**A IA não é a memória. O banco é.**

A tentação é jogar o histórico do grupo no contexto e deixar o modelo lembrar.
Isso funciona por duas semanas e depois quebra: o "aluguel dia 23" some no meio
de milhares de mensagens, o custo por mensagem só cresce, e — o pior — ela
responde com confiança uma coisa errada.

Aqui a IA é só a **porta de entrada em português**. Ela traduz o que você fala
em leitura e escrita num SQLite. Quem lembra é o banco. A IA pode ser trocada
amanhã sem perder nada.

---

## 2. A máquina — JÁ ESTÁ PRONTA

Não precisa criar nada. A máquina existe, foi conferida e está registrada como
brain no painel. Estado verificado em 07/09/2026:

| Item | Valor |
|---|---|
| Hostname | `lauren-assistente` |
| IP | 10.0.0.Y |
| SO | Ubuntu 22.04.5 LTS |
| vCPU | 2 (Xeon E5-2403 — **não é kvm64**, ok) |
| RAM | 3,9 GB |
| Disco | 39 GB, 7% usado |
| Python | 3.10.12 |
| Módulo `sqlite3` do Python | funciona (SQLite 3.37.2) |
| Saída pra internet | ok |
| **Diretório de trabalho** | **`/opt/lauren-assistente`** |

> O documento fala em Python 3.11+ em alguns lugares por hábito. **3.10 basta** —
> nada aqui usa recurso de 3.11. Não atualizar Python.

---

## 3. Pacotes

**Regra da casa: confirmar com o dono ANTES de instalar qualquer coisa.**
Ele audita o que entra nas máquinas. Não rodar `apt install` por conta própria.

### Agora (passos 1 a 6) — um pacote só

```bash
apt install sqlite3      # SÓ o CLI, e só depois de autorizado
```

O código não precisa disso: o módulo `sqlite3` do Python já funciona nesta
máquina. O CLI serve pra **você e o dono conferirem o banco na mão** durante os
testes do passo 12 (`sqlite3 casa.db "select * from contas"`).

Se a autorização não vier, siga assim mesmo — dá pra conferir por Python:

```bash
python3 -c "import sqlite3;print(sqlite3.connect('casa.db').execute('select * from contas').fetchall())"
```

### Depois (passo 8, quando a API da Lauren existir)

`python3-venv`, `python3-pip` e o pacote `openai`. **Não instalar agora** — não
faz falta nenhuma nos passos 1 a 6, e o formato da API ainda não foi decidido.

**NÃO instalar nginx** em hipótese alguma. O proxy e o SSL são feitos pelo
painel da VPS, fora desta máquina.

---

## 4. Estrutura de arquivos

```
/opt/lauren-assistente/
├── assistente.py      # servidor: recebe o webhook, roteia
├── cerebro.py         # camada da IA — a ÚNICA parte que sabe qual modelo é
├── ferramentas.py     # as 8 funções que a IA pode chamar
├── lembretes.py       # roda no cron, avisa de conta vencendo
├── schema.sql         # o banco
├── config.json        # chmod 600 — chaves e IDs
├── casa.db            # chmod 600 — os dados
└── venv/
```

`cerebro.py` isolado é de propósito: trocar de modelo (ou sair da Lauren pra
outra coisa) mexe **num arquivo só**.

---

## 5. O banco — `schema.sql`

Esta é a parte mais importante do documento. O desenho das duas primeiras
tabelas é o que separa um assistente confiável de um que te faz perder o
aluguel.

```sql
-- A REGRA: "aluguel, todo dia 23, R$ 2300"
CREATE TABLE IF NOT EXISTS contas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    nome            TEXT NOT NULL UNIQUE,
    dia_vencimento  INTEGER,              -- 1 a 31
    valor           REAL,
    recorrente      INTEGER DEFAULT 1,    -- 1 = todo mês
    ativo           INTEGER DEFAULT 1,
    criado_em       TEXT,
    criado_por      TEXT
);

-- O EVENTO: "o aluguel DE SETEMBRO foi pago dia 20"
CREATE TABLE IF NOT EXISTS pagamentos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conta_id        INTEGER NOT NULL REFERENCES contas(id),
    competencia     TEXT NOT NULL,        -- 'YYYY-MM' — a QUAL mês se refere
    pago_em         TEXT,
    valor_pago      REAL,
    registrado_por  TEXT,
    UNIQUE(conta_id, competencia)         -- impede pagar 2x o mesmo mês
);

-- Lista de compras: NUNCA apaga, só marca
CREATE TABLE IF NOT EXISTS lista_compras (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    item          TEXT NOT NULL,
    quem_pediu    TEXT,
    criado_em     TEXT,
    comprado      INTEGER DEFAULT 0,
    comprado_em   TEXT,
    comprado_por  TEXT
);

-- Qualquer outra coisa: senha do wifi, revisão do carro, aniversário
CREATE TABLE IF NOT EXISTS fatos (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    assunto        TEXT NOT NULL,
    valor          TEXT NOT NULL,
    quem_disse     TEXT,
    criado_em      TEXT,
    atualizado_em  TEXT
);

-- Auditoria: toda mensagem e toda resposta
CREATE TABLE IF NOT EXISTS historico (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT,
    quem         TEXT,
    mensagem     TEXT,
    resposta     TEXT,
    ferramentas  TEXT,
    tokens_in    INTEGER DEFAULT 0,
    tokens_out   INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_pag_comp  ON pagamentos(competencia);
CREATE INDEX IF NOT EXISTS idx_lista_ok  ON lista_compras(comprado);
CREATE INDEX IF NOT EXISTS idx_fatos_ass ON fatos(assunto);
```

### Por que `contas` e `pagamentos` são separadas

Parece burocracia, mas não é.

Se você guardar `pago = 1` **na conta**, funciona lindamente em setembro. Aí
chega outubro, você pergunta *"o aluguel já foi pago?"* e ela responde
**"já foi pago sim"** — porque o campo continua marcado do mês passado.

Você perde o aluguel e a culpa é do modelo de dados.

"Já foi pago?" é **sempre** uma pergunta sobre um mês específico. Por isso o
pagamento é um evento com `competencia = '2026-10'`, e a resposta é uma busca:

```sql
SELECT 1 FROM pagamentos
 WHERE conta_id = ? AND competencia = strftime('%Y-%m','now','localtime');
```

Essa armadilha é invisível até o mês virar. É o erro nº 1 desse tipo de projeto.

### Por que a lista não apaga

`comprado = 1` em vez de `DELETE`. Duas vantagens: "o que falta" é só filtrar
`comprado = 0`, e você ganha de brinde o histórico — daqui a seis meses dá pra
ver que vocês compram sabão a cada três semanas.

---

## 6. As ferramentas que a IA ganha

Oito funções. É tudo que ela pode fazer — ela não tem shell, não tem rede, não
tem nada além disto:

| Ferramenta | O que faz |
|---|---|
| `salvar_conta(nome, dia, valor)` | Cria/atualiza a regra da conta |
| `marcar_pago(nome, competencia?)` | Registra pagamento (padrão: mês atual) |
| `consultar_contas(nome?)` | Lista contas + se o mês atual já foi pago |
| `lista_add(item)` | Põe item na lista de compras |
| `lista_marcar_comprado(item)` | Marca como comprado |
| `lista_ver()` | O que ainda falta |
| `lembrar_fato(assunto, valor)` | Guarda um fato solto |
| `buscar_fato(termo)` | Procura nos fatos |

### Regra de ouro da resposta

**Ela sempre confirma o que gravou.**

> "Anotei: aluguel, todo dia 23, R$ 2300."

Se ela entendeu `2300` como `23,00`, você descobre **na hora** e corrige — não
daqui a um mês quando a conta vencer. Sem essa confirmação, o erro fica
escondido no banco.

---

## 7. `config.json` (chmod 600)

```json
{
  "lauren": {
    "base_url": "https://SEU-ENDPOINT-DA-LAUREN/v1",
    "api_key":  "COLAR-AQUI",
    "modelo":   "DEFINIR"
  },
  "whatsapp": {
    "grupo_jid":      "1203634XXXXXXXXX@g.us",
    "webhook_secret": "GERAR-COM-openssl-rand-base64-32",
    "api_url":        "http://10.0.0.X/api/sms",
    "api_key":        "CHAVE-DO-CANAL",
    "api_token":      "TOKEN-DO-CANAL"
  },
  "assistente": {
    "wake_word":          "lauren",
    "responder_sem_wake": false,
    "porta":              8090
  },
  "pessoas": {
    "5511XXXXXXXXX": "Johnata",
    "5511YYYYYYYYY": "Esposa"
  }
}
```

`pessoas` serve pra ela saber quem falou — "quem pediu o sabão foi ela".

---

## 8. Ligar no WhatsApp

1. Criar um **canal novo** no HuberChat só pra esse número (não reaproveitar o
   canal Lauren do StarCore — são coisas diferentes)
2. Webhook do canal aponta direto pro **IP interno** desta máquina:

   ```
   http://10.0.0.Y:8090/webhook
   ```

3. **Não publicar na internet. Não usar domínio. Não configurar SSL.**

   Verificado em 07/09/2026: o HuberChat (10.0.0.X) e esta máquina
   (10.0.0.Y) se enxergam na rede interna — ping de 0,9 ms. O webhook nunca
   sai da rede do dono, então não há o que expor. Menos peça, menos superfície,
   e nada de certificado pra vencer.

   O servidor deve escutar em `0.0.0.0:8090` (para aceitar do HuberChat), e a
   porta **não** deve ser liberada no firewall de borda
4. O `webhook_secret` do config tem que ser o mesmo cadastrado no canal — a
   assinatura `X-HuberChat-Signature` (HMAC-SHA256) é validada em toda requisição

### Decisão pendente do passo 3

A API da Lauren é compatível com o formato OpenAI (`/v1/chat/completions` com
`tools` e `tool_calls`)?

- **Se sim:** o pacote `openai` funciona apontando `base_url` pra ela. Pronto.
- **Se não:** `cerebro.py` vira uma chamada `urllib` no formato dela — mesmo
  trabalho, arquivo isolado, nada mais muda.

**Isto é o que precisa ser confirmado antes de escrever o `cerebro.py`.**

> ⚠️ O ponto crítico não é a conversa, é o **tool calling**. Se o gateway só faz
> proxy de chat e não implementa o ciclo completo (modelo pede a ferramenta →
> recebe o resultado → continua a resposta), o assistente fica capenga de um
> jeito traiçoeiro: você fala "já comprei sabão", ela responde
> "beleza, marquei!" — **e não grava nada**. Testar isso no passo 12 antes de
> confiar.

---

## 9. Lembretes automáticos

O que transforma "chatbot com memória" em assistente de verdade: ela avisa
sozinha, sem ninguém perguntar.

`lembretes.py` roda 1x por dia, olha as contas dos próximos 3 dias e manda no
grupo o que ainda não foi pago **neste mês**:

```
📅 Aluguel vence dia 23 (daqui a 2 dias) — R$ 2300.
   Ninguém marcou como pago ainda.
```

```cron
0 9 * * * /opt/lauren-assistente/venv/bin/python3 /opt/lauren-assistente/lembretes.py
```

---

## 10. Segurança

Regras não-negociáveis desta máquina:

1. **Zero comando de rede.** Nada de `/failover`, `/rota`, `/auto`. Esses
   comandos são perigosos e vivem só no grupo STARCORE. Esta máquina nem tem
   acesso SSH aos MikroTiks.
2. **Um grupo só.** Qualquer JID diferente do `grupo_jid` do config é ignorado
   em silêncio. Mensagem no privado também é ignorada.
3. **`config.json` e `casa.db` com chmod 600.**
4. **Assinatura do webhook validada sempre** — sem assinatura válida, descarta.
5. **Isolada do painel StarCore.** Esses dados são da sua casa, não da empresa.
   Sua equipe mexe no painel; não deve esbarrar nas contas da sua casa.
6. **Porta só na rede interna.** O servidor escuta em `0.0.0.0:8090` porque o
   HuberChat (10.0.0.X) precisa alcançar, mas a porta **não** é liberada no
   firewall de borda e **não** é publicada por domínio nem proxy. O webhook
   nunca sai da rede do dono — ver seção 8.

### Wake word

Com `responder_sem_wake: false`, ela **só responde quando é chamada pelo nome**.
Ela não lê nem comenta a conversa de vocês dois.

Isso é escolha consciente, e recomendo manter assim: menos invasivo pra sua
esposa, mais barato, e ela não se mete onde não foi chamada.

---

## 11. Ordem de execução

```
[ ] 1. Criar a VM (CPU host, não kvm64)
[ ] 2. Instalar os pacotes do passo 3
[ ] 3. Criar /opt/lauren-assistente + venv
[ ] 4. Aplicar o schema.sql        → sqlite3 casa.db < schema.sql
[ ] 5. Escrever ferramentas.py     (as 8 funções, sem IA nenhuma)
[ ] 6. TESTAR as ferramentas direto no Python — sem IA, sem WhatsApp

--------  ⛔ PARE AQUI. Do 7 em diante depende da API da Lauren.  --------

[ ] 7. Confirmar o formato da API da Lauren (passo 8)
[ ] 8. Escrever cerebro.py
[ ] 9. TESTAR no terminal          → python3 assistente.py --terminal
[ ] 10. Criar canal + grupo no HuberChat
[ ] 11. Preencher config.json
[ ] 12. Escrever assistente.py (webhook) e subir o systemd
[ ] 13. TESTAR no grupo
[ ] 14. Ligar o cron dos lembretes
[ ] 15. Registrar a máquina como brain no painel
```

Os passos 6, 9 e 13 são pontos de parada. Não passar adiante com um deles
falhando.

### O que o passo 6 tem que provar

Chamando as funções direto no Python, sem IA nenhuma. Cada linha abaixo é uma
verificação no banco, não uma impressão na tela:

```
[ ] salvar_conta("aluguel", 23, 2300)      → linha em contas, valor == 2300.0
[ ] consultar_contas("aluguel")            → diz que NÃO foi pago este mês
[ ] marcar_pago("aluguel")                 → linha em pagamentos, competencia == mês atual
[ ] consultar_contas("aluguel")            → agora diz que FOI pago
[ ] marcar_pago("aluguel") de novo         → NÃO cria linha duplicada (UNIQUE segura)
[ ] lista_add("sabão") + lista_add("cabeça de chuveiro")
[ ] lista_ver()                            → devolve os dois
[ ] lista_marcar_comprado("sabão")         → comprado == 1, linha CONTINUA existindo
[ ] lista_ver()                            → devolve só a cabeça de chuveiro
[ ] lembrar_fato("wifi", "senha123") + buscar_fato("wifi")
```

E o teste da virada do mês (seção 12) — esse é obrigatório no passo 6 também,
porque é lógica pura, não depende de IA.

---

## 12. Como testar antes de ligar no WhatsApp

`assistente.py --terminal` conversa por linha de comando, mesmo cérebro e mesmo
banco, sem WhatsApp nenhum. O roteiro mínimo:

```
> Lauren, meu aluguel é dia 23 todo mês, valor 2300
  ✔ Ela confirma o que gravou, com o valor certo (2300, não 23)
  ✔ sqlite3 casa.db "select * from contas"  →  a linha existe

> Lauren, meu aluguel vence quando?
  ✔ Responde dia 23 e diz quantos dias faltam

> Lauren, meu aluguel já foi pago?
  ✔ Responde que NÃO

> Lauren, paguei o aluguel
  ✔ sqlite3 casa.db "select * from pagamentos"  →  competencia = mês atual

> Lauren, meu aluguel já foi pago?
  ✔ Agora responde que SIM

> Lauren, falta comprar sabão e cabeça de chuveiro
> Lauren, o que falta comprar?
  ✔ Lista os dois

> Lauren, já comprei sabão
> Lauren, o que falta comprar?
  ✔ Lista SÓ a cabeça de chuveiro
```

**O teste que realmente importa** é o penúltimo par. Se ela responder
"marquei!" mas o `sqlite3 casa.db "select * from lista_compras"` mostrar
`comprado = 0`, o tool calling não está funcionando — ela está *fingindo*. É
exatamente a falha do passo 8 e é preciso resolver antes de ligar no grupo.

### O teste da virada do mês

Vale rodar uma vez, na mão:

```sql
-- simula que o pagamento foi do mês passado
UPDATE pagamentos SET competencia = '2026-08' WHERE conta_id = 1;
```

Agora pergunte de novo "o aluguel já foi pago?". Ela **tem** que responder que
não. Se responder que sim, o modelo de dados foi implementado errado.

---

## Resumo do que precisa de você

1. A VM criada (specs do passo 2)
2. **Endpoint + API key da Lauren**, e se ela aceita o formato OpenAI com `tools`
3. Qual modelo usar
4. Número novo + canal + grupo no HuberChat
5. Os dois números (seu e da sua esposa) pro campo `pessoas`

O resto é montagem.
