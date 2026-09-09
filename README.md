<div align="center">

# 🏠 Lauren — Assistente de Casa

**Um assistente que vive no grupo de WhatsApp da família e lembra das coisas da casa.**
Contas a pagar · lista de compras · tarefas · dinheiro

*Python puro. Sem framework, sem venv, sem uma única dependência externa.*

</div>

---

```
Você:   Lauren, meu aluguel é dia 23 todo mês, valor 2300
Lauren: ✅ Conta salva!
        📄 Aluguel
        📅 Todo dia 23
        💸 R$ 2.300,00

        (duas semanas depois)

Você:   Lauren, meu aluguel já foi pago?
Lauren: 📄 Aluguel
        📅 Vence dia 23 — daqui a 4 dias
        💸 R$ 2.300,00
        ❌ Ainda não foi pago em setembro
```

---

## A regra que faz isso funcionar

> ### A IA não é a memória. **O banco é.**

A tentação é jogar o histórico do grupo no contexto e deixar o modelo lembrar.
Funciona por duas semanas e depois quebra: o *"aluguel dia 23"* some no meio de
milhares de mensagens, o custo por mensagem só cresce, e — o pior — ela responde
com confiança uma coisa errada.

Aqui a IA é só a **porta de entrada em português**. Ela traduz o que você fala em
leitura e escrita num SQLite. Quem lembra é o banco. **O modelo pode ser trocado
amanhã sem perder um dado.**

---

## O que ela faz

| | |
|---|---|
| 📄 **Contas a pagar** | Aluguel, luz, água. Avisa 3 dias antes e continua avisando se atrasar |
| ✅ **Tarefas** | Com data, com recorrência (*toda segunda*), e cobrança à noite |
| 🛒 **Lista de compras** | Nunca apaga — marca como comprado e guarda o histórico |
| 💰 **Dinheiro** | Saldo por banco, gasto por categoria, entrada, estorno, parcelamento |
| 🎯 **Limites** | Teto mensal por categoria, com aviso ao chegar em 80% |
| 🔖 **Fatos** | Senha do wifi, revisão do carro, o que for |

### Ela fala sozinha

**06:00** — o que vence hoje, o que está atrasado, o que estourou o limite
**21:00** — *"sobraram 2 tarefas de hoje, deu pra fazer alguma?"*
**dia 1º** — o fechamento do mês que acabou

---

## As três armadilhas que este projeto evita

<table>
<tr><td width="33%" valign="top">

### 💀 O "pago" na conta

Se `pago` for um campo **na conta**, funciona lindamente em setembro. Em outubro
você pergunta *"o aluguel já foi pago?"* e ela responde **"já"** — porque o campo
ficou marcado do mês passado.

**Você perde o aluguel e a culpa é do modelo de dados.**

Aqui pagamento é um **evento** com `competencia = '2026-10'`, e a resposta é uma
busca. Invisível até o mês virar.

</td><td width="33%" valign="top">

### 💀 O saldo em coluna

Guardar `saldo` no banco dessincroniza no primeiro erro, não desfaz direito, e
apaga a resposta de *"onde foi meu dinheiro?"* — que é metade da graça de anotar
gasto.

Aqui o saldo **não existe**: é `SUM(valor)` dos movimentos não estornados, e só
conta o que **já aconteceu** — parcela de dezembro não sai do bolso hoje.

</td><td width="33%" valign="top">

### 💀 O "marquei!" que não gravou

O erro mais traiçoeiro de assistente com IA: ela responde *"beleza, marquei!"*
e **não grava nada**.

Aqui toda ferramenta devolve o que gravou, e a instrução é explícita: nunca
dizer que anotou sem ter recebido `ok: true`. Quando não consegue, ela **diz que
não conseguiu** — foi isso que permitiu consertar todos os erros conversando.

</td></tr>
</table>

---

## Arquitetura

```
   WhatsApp                    esta máquina
  ┌─────────┐                ┌──────────────────────────────────────┐
  │  grupo  │                │                                      │
  │ da casa │   webhook      │  assistente.py ── valida assinatura  │
  └────┬────┘  assinado      │        │          filtra o grupo      │
       │      HMAC-SHA256    │        │          aplica wake word    │
       ▼                     │        ▼                              │
  ┌─────────┐                │   cerebro.py ──── a ÚNICA parte que  │
  │HuberChat├───────────────▶│        │          sabe qual modelo é  │
  └─────────┘                │        ▼                              │
       ▲                     │  ferramentas.py ─ 28 funções puras    │
       │  resposta           │        │                              │
       └─────────────────────┤        ▼                              │
                             │     casa.db  ◀── quem lembra          │
                             │                                       │
                             │  lembretes.py ─── cron 6h e 21h       │
                             │  alerta.py ────── avisa se cair       │
                             └──────────────────────────────────────┘
```

`cerebro.py` isolado é de propósito: **trocar de modelo mexe num arquivo só.**

---

## O banco

**10 tabelas.** As duas primeiras são o coração:

```sql
-- A REGRA: "aluguel, todo dia 23, R$ 2300"
CREATE TABLE contas (
    nome TEXT NOT NULL UNIQUE, dia_vencimento INTEGER, valor REAL, ativo INTEGER
);

-- O EVENTO: "o aluguel DE SETEMBRO foi pago dia 20"
CREATE TABLE pagamentos (
    conta_id INTEGER NOT NULL REFERENCES contas(id),
    competencia TEXT NOT NULL,              -- 'AAAA-MM' — a QUAL mês se refere
    UNIQUE(conta_id, competencia)           -- impede pagar 2x o mesmo mês
);
```

> *"Já foi pago?"* é **sempre** uma pergunta sobre um mês específico.

As outras: `lista_compras`, `tarefas`, `fatos`, `bancos`, `movimentos`,
`limites`, `fila`, `historico`.

**Nada se apaga.** Item comprado vira `comprado = 1`, lançamento errado vira
`estornado = 1`, conta que acabou vira `ativo = 0`. O histórico fica.

---

## As 28 ferramentas

<details>
<summary><b>Contas a pagar</b></summary>

`salvar_conta` · `marcar_pago` · `desmarcar_pago` · `consultar_contas` · `conta_desativar`
</details>

<details>
<summary><b>Tarefas</b></summary>

`tarefa_add` (com recorrência) · `tarefas_ver` · `tarefa_feita` · `tarefa_corrigir`
</details>

<details>
<summary><b>Lista de compras</b></summary>

`lista_add` · `lista_ver` · `lista_marcar_comprado` · `lista_corrigir`
</details>

<details>
<summary><b>Dinheiro</b></summary>

`banco_salvar` · `gasto_registrar` · `entrada_registrar` · `compra_parcelada` ·
`saldo_ver` · `extrato` · `resumo` · `quanto_sobra` · `estornar` ·
`parcelas_cancelar` · `limite_definir`
</details>

<details>
<summary><b>Fatos e auditoria</b></summary>

`lembrar_fato` · `buscar_fato` · `esquecer_fato` · `historico_ver`
</details>

**Toda operação de criar tem a de corrigir do lado.** Isso não é simetria
estética — sem a ferramenta de correção, o modelo improvisa e grava dado falso.
Aconteceu, está documentado, e foi assim que virou regra.

---

## Segurança

- **Assinatura HMAC-SHA256** sobre `"<timestamp>." + corpo cru`, com janela de 5
  minutos contra repetição de requisição capturada
- **Um grupo só** — qualquer outro JID é ignorado em silêncio
- **Wake word** — ela não lê nem comenta a conversa da família
- **`fromMe` ignorado** — senão ela ouve a própria voz e vira laço infinito
- **Nada exposto na internet** — sem domínio, sem SSL, sem proxy; o webhook não
  sai da rede interna
- `config.json` e `casa.db` em `chmod 600`; a unit roda com `ProtectSystem=strict`

---

## Testes

```console
$ python3 teste_ferramentas.py     29/29    as 8 funções base, sem IA
$ python3 teste_dinheiro.py        49/49    saldo, estorno, limites, virada do mês
$ python3 teste_tarefas.py         30/30    prazo, recorrência, aviso
$ python3 teste_desfazer.py        45/45    desfazer, aposentar, arquivar
$ python3 teste_parcelas.py        26/26    parcelamento e histórico
$ python3 teste_webhook.py         57/57    assinatura, replay, roteamento HTTP
$ python3 teste_roteamento.py      18/18    frase → ferramenta certa (usa a API)
```

**254 verificações.** Todas leem o banco por SQL — nenhuma confia no que a
função devolveu. Rodam em banco descartável: `casa.db` nunca é tocada.

O teste que mais importa continua sendo o mais simples:

> Dizer **"Lauren, já comprei sabão"**, ela responder *"marquei"*, e o
> `sqlite3 casa.db "select * from lista_compras"` mostrar `comprado = 1`.
> Se mostrar `0`, ela está fingindo.

---

## Instalação

Requisitos: **Python 3.10+** e nada mais. Nem pip.

```bash
git clone https://github.com/LinkjohnDEV/Assistente-Pessoal.git /opt/lauren-assistente
cd /opt/lauren-assistente

python3 -c "import sqlite3; sqlite3.connect('casa.db').executescript(open('schema.sql').read())"
chmod 600 casa.db

cp config.json.exemplo config.json && chmod 600 config.json
$EDITOR config.json          # o código RECUSA rodar com placeholder

python3 assistente.py --terminal      # conversa pelo teclado, mesmo banco
```

Subindo de verdade:

```bash
cp lauren-assistente.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now lauren-assistente

crontab -e
```
```cron
CRON_TZ=America/Sao_Paulo
0  6 * * * /usr/bin/python3 /opt/lauren-assistente/lembretes.py          >> lembretes.log 2>&1
0 21 * * * /usr/bin/python3 /opt/lauren-assistente/lembretes.py --noite  >> lembretes.log 2>&1
```

> ⚠️ **`CRON_TZ` não é opcional.** O daemon do cron lê o fuso quando sobe; se
> subiu antes do fuso estar definido, fica em UTC pra sempre e o `timedatectl`
> não denuncia. O aviso das 6h saiu às 3h da manhã por causa disso.
> **Prove com um job de teste 2 minutos à frente.**

---

## Trocar de modelo

`cerebro.py` fala o padrão *chat completions*. Qualquer provedor que fale o
mesmo padrão funciona trocando duas linhas do `config.json`:

```json
"lauren": { "base_url": "https://…/v1", "api_key": "…", "modelo": "…" }
```

Nenhum outro arquivo muda.

---

## Como este projeto foi construído

Foi ao ar num grupo de WhatsApp de verdade e ficou lá sendo usado enquanto era
escrito. **Todos os buracos encontrados apareceram com gente usando — nenhum nos
testes.** Alguns:

| o que aconteceu | o conserto |
|---|---|
| Pediram pra renomear item; sem ferramenta de correção, ela marcou o errado como comprado e criou outro — **gravou uma compra que não houve** | `lista_corrigir`, e a regra de criar+corrigir sempre juntos |
| `"fechadura da porta"` não achava `"fechadura porta"` — o `da` quebrava o `LIKE` | casamento por palavra inteira, sem acento |
| `"x"` casava com `"levar o lixo pra rua"` (por causa do `lixo`) | pedaço tem que ser palavra inteira e ter 4+ letras |
| Cadastrar banco com saldo zero dava erro **e criava o banco assim mesmo** | validar antes de inserir; mensagem e banco discordando é pior que os dois errados |
| O aviso das 6h saiu às 3h | `CRON_TZ` explícito, provado com job de teste |
| Uma requisição válida não deixava rastro no log — sucesso e "nunca chegou" eram indistinguíveis | toda aceitação escreve no log |

O `MEMORY.md` guarda as 15 armadilhas com sintoma, causa e conserto. É o
documento mais útil do repositório.

---

<div align="center">

**Feito para uma casa, com dados de uma casa.**

*Se você for usar, comece pelo `MEMORY.md` — ele custou mais caro que o código.*

</div>
