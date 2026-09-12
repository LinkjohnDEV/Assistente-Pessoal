# MEMORY.md — Assistente de Casa (Lauren)

Estado do projeto e decisões tomadas. Escrito em 07/09/2026 e usado no grupo
no mesmo dia — as armadilhas da seção 10 vieram todas de uso real, nenhuma de
teste.
A especificação original é o `assistentecasa.md`; **este arquivo é o que
mudou, o que foi decidido e o que doeu.** Se os dois discordarem, este é o
mais novo.

> ⚠️ **Nenhum segredo mora aqui.** A chave da Lauren, o segredo do webhook e a
> chave/token do HuberChat estão no `config.json` (chmod 600). Este arquivo é
> legível por todos — não colar credencial nele.

---

## 1. O que é

Assistente que vive no grupo de WhatsApp da família e lembra das coisas da
casa: contas a pagar, lista de compras, fatos soltos.

**A regra que sustenta tudo: a IA não é a memória. O banco é.**
A IA é só a porta de entrada em português — traduz fala em leitura e escrita
num SQLite. Pode ser trocada amanhã sem perder nada.

---

## 2. A máquina

| Item | Valor |
|---|---|
| Hostname | `lauren-assistente` |
| IP | 10.0.0.Y |
| SO | Ubuntu 22.04.5 LTS |
| Python | 3.10.12 |
| Diretório | `/opt/lauren-assistente` |

**Não tem venv, não tem pip, não tem o pacote `openai`.** Tudo roda na
biblioteca padrão do Python — `urllib`, `hmac`, `sqlite3`, `http.server`.
Foi decisão consciente: a API da Lauren fala o padrão chat completions, e o
laço de tool calling inteiro cabe em `urllib`. Menos peça pra instalar, menos
pra auditar, menos pra quebrar.

Único pacote instalado: `sqlite3` (o CLI), autorizado pelo dono. O código não
precisa dele — o módulo `sqlite3` do Python já vem. Serve pra conferir o banco
na mão.

**Regra da casa: confirmar com o dono ANTES de instalar qualquer coisa.**
NÃO instalar nginx — proxy e SSL não existem neste desenho.

---

## 3. Arquivos

```
/opt/lauren-assistente/
├── assistente.py            servidor: webhook + assinatura + modo terminal
├── cerebro.py               a camada da IA — o ÚNICO arquivo que sabe qual modelo é
├── ferramentas.py           as 8 funções que a IA pode chamar
├── lembretes.py             cron 6h: conta vencendo/vencida, tarefa do dia,
│                           limite estourado, fechamento no dia 1º,
│                           arquivamento do histórico (NÃO usa IA)
├── alerta.py                avisa no grupo se o serviço cair (OnFailure)
├── schema.sql               o banco
├── config.json              chmod 600 — chaves e IDs
├── casa.db                  chmod 600 — os dados
├── teste_ferramentas.py     29 verificações, banco descartável
├── teste_dinheiro.py        49 verificações, banco descartável
├── teste_tarefas.py         30 verificações, banco descartável
├── teste_desfazer.py        34 verificações, banco descartável
├── teste_webhook.py         57 verificações, sobe na porta 8091
├── lauren-assistente.service  a unit (cópia; a de verdade está em /etc/systemd/system/)
├── assistentecasa.md        a especificação original
└── MEMORY.md                este arquivo
```

`cerebro.py` isolado é de propósito: trocar de modelo mexe num arquivo só.

---

## 4. O banco

Dez tabelas: `contas`, `pagamentos`, `lista_compras`, `fatos`, `historico`,
`bancos`, `movimentos`, `limites`, `tarefas`, `fila`.
Schema completo em `schema.sql`.

### Por que `contas` e `pagamentos` são separadas — o erro nº 1

Se o "pago" for um campo **na conta**, funciona lindamente em setembro. Em
outubro você pergunta "o aluguel já foi pago?" e ela responde "já" — porque o
campo ficou marcado do mês passado. Você perde o aluguel e a culpa é do modelo
de dados.

"Já foi pago?" é **sempre** pergunta sobre um mês específico. Pagamento é um
evento com `competencia = 'AAAA-MM'`, e a resposta é uma busca:

```sql
SELECT 1 FROM pagamentos
 WHERE conta_id = ? AND competencia = strftime('%Y-%m','now','localtime');
```

`UNIQUE(conta_id, competencia)` impede pagar duas vezes o mesmo mês.
**Essa armadilha é invisível até o mês virar.** Tem teste pra ela.

### Por que o saldo não é coluna (07/09/2026)

Mesma lição, terceira vez. `bancos` não tem campo `saldo`:

```sql
SELECT SUM(valor) FROM movimentos WHERE banco_id = ? AND estornado = 0
```

Saldo guardado num campo dessincroniza no primeiro erro, não desfaz direito, e
apaga a resposta de "onde foi meu dinheiro?" — que é metade da graça de anotar
gasto. `valor` negativo é saída, positivo é entrada.

Duas sutilezas que já custaram teste:

- **`quando` (data do gasto) é separado de `registrado_em` (data em que foi
  digitado).** "Ontem gastei 50" tem que somar em ontem. A `competencia` sai de
  `quando`, nunca de hoje.
- **`Saldo inicial` não conta como entrada do mês.** Sem isso, "quanto entrou
  em setembro" responde 4.400 quando o salário foi 3.200.

Estorno é `estornado = 1`, nunca `DELETE` — some da soma, fica no histórico.
A coluna `estorno_de` está reservada e hoje fica sempre NULL.

### Por que a lista não apaga

`comprado = 1` em vez de `DELETE`. "O que falta" é filtrar `comprado = 0`, e
de brinde vem o histórico — daqui a seis meses dá pra ver de quanto em quanto
tempo compram sabão.

---

## 5. As 32 ferramentas

| Ferramenta | O que faz |
|---|---|
| `salvar_conta(nome, dia, valor)` | Cria/atualiza a regra da conta |
| `marcar_pago(nome, competencia?)` | Registra pagamento (padrão: mês atual) |
| `consultar_contas(nome?)` | Contas + se o mês atual já foi pago + dias pra vencer |
| `lista_add(item)` | Põe item na lista |
| `lista_marcar_comprado(item)` | Marca como comprado |
| `lista_ver()` | O que ainda falta |
| `lembrar_fato(assunto, valor)` | Guarda um fato solto |
| `buscar_fato(termo)` | Procura nos fatos |
| `lista_corrigir(item, novo)` | Troca o NOME do item (ver armadilha 7) |
| `banco_salvar(nome, tipo?, saldo_inicial?)` | Cadastra banco/cartão/dinheiro |
| `gasto_registrar(valor, descricao?, banco?, categoria?, quando?)` | Saída |
| `entrada_registrar(valor, descricao?, banco?, quando?)` | Entrada |
| `saldo_ver(banco?)` | Quanto tem — SOMA, nunca campo |
| `extrato(banco?, competencia?, categoria?)` | Lançamentos do mês |
| `resumo(competencia?)` | Total por categoria, com limite ao lado |
| `estornar(id)` | Desfaz lançamento |
| `limite_definir(categoria, valor_mes)` | Teto mensal por categoria |
| `tarefa_add(tarefa, quando?, de_quem?)` | Coisa a fazer, com ou sem prazo |
| `tarefas_ver(incluir_feitas?, ate?)` | O que falta, com dias restantes |
| `tarefa_feita(tarefa)` | Marca feita — não apaga |
| `tarefa_corrigir(tarefa, novo_texto?, novo_quando?)` | Muda texto e/ou data |
| `desmarcar_pago(nome, competencia?)` | Desfaz pagamento de conta |
| `conta_desativar(nome)` | Aposenta conta que não se paga mais |
| `conta_pular(nome, competencia?)` | Este mês não tem a conta — `pagamentos.situacao = 'pulado'` |
| `transferir(valor, de?, para?)` | Move entre conta e caixinha — **não é gasto** |
| `banco_desativar(nome)` | Aposenta banco; o histórico fica |
| `esquecer_fato(assunto)` | Apaga fato vencido |
| `quanto_sobra()` | Saldo menos as contas fixas ainda não pagas |
| `compra_parcelada(...)` | "10x de 300" → 10 lançamentos, um por mês |
| `parcelas_cancelar(descricao)` | Mata as parcelas que ainda não venceram |
| `gastos_periodo(desde, ate?)` | Gasto entre duas datas, atravessando meses |
| `historico_ver(dias?)` | O que a Lauren registrou — auditoria de dentro do grupo |

`lista_add` aceita `onde` (mercado/casa) e `valor`, os dois **opcionais** — item
sem eles entra igual e fica de fora da soma. O total sempre diz quantos itens
entraram nele.

`tarefa_add` aceita `repete` (diaria/semanal/mensal): ao marcar feita, a
próxima nasce sozinha e a antiga fica como histórico. `resumo` aceita
`comparar_com` (outro AAAA-MM) e traz a variação por categoria.

`marcar_pago` aceita `banco`: paga a conta **e** lança o gasto, senão o saldo
mente pra quem paga conta pelo grupo.

**A única linha que se APAGA de verdade** é a de `pagamentos`, no
`desmarcar_pago` — porque `UNIQUE(conta_id, competencia)` faria uma linha
"estornada" bloquear o pagamento verdadeiro quando ele acontecesse. Fatos
também se apagam. Todo o resto marca e guarda.

**Três coisas parecidas, que não são a mesma:** `fatos` guarda e **nunca**
avisa (senha do wifi); `tarefas` tem data e entra no aviso das 6h (revisar o
carro dia 12); `contas` é o que se paga todo mês. Confundir as três foi o que
fez a Lauren sugerir "diga: lembre que preciso revisar o carro" — que virava
fato e nunca avisava ninguém.

Categorias são **lista fechada** (`ferramentas.CATEGORIAS`): Alimentação,
Mercado, Transporte, Moradia, Saúde, Educação, Lazer, Vestuário, Assinaturas,
Pets, Contas e utilidades, Outros. Livre viraria "Transporte", "transporte" e
"Locomoção" — três linhas no relatório pra mesma coisa.

### Decisões da aplicação (schema NÃO mudou por causa delas)

1. **Nomes normalizados** — minúscula, espaços aparados, em conta, item e
   assunto. Sem isso "Aluguel" e "aluguel" viram duas contas e o `UNIQUE` não
   segura. Isto tapou um buraco do documento original.
2. **`lista_marcar_comprado` aceita pedaço do nome** — "chuveiro" acha "cabeça
   de chuveiro". Se não achar, **devolve erro em vez de fingir**.
3. **`lembrar_fato` no mesmo assunto atualiza** em vez de empilhar — é pra isso
   que existe o campo `atualizado_em`.
4. **O parâmetro `quem` não é exposto no esquema da IA.** Quem falou vem do
   `from` da mensagem, não da opinião do modelo — ele não pode dizer que foi a
   esposa.

### Regra de ouro da resposta

**Ela sempre confirma o que gravou, com os valores.**
"Anotei: aluguel, todo dia 23, R$ 2300." Se entendeu 2300 como 23, você
descobre na hora — não daqui a um mês.

---

## 6. A API da Lauren

Documentação: https://ialauren.com/docs/api

- `POST https://ialauren.com/v1/chat/completions`, `Authorization: Bearer lrn_...`
- Padrão chat completions, com o ciclo completo de tool calling
- Modelo em uso: **`lauren-4`**. Outros: `nebula-3`, `velix-5` (PRO+),
  `lauren-6` (MAX+, não aparece em `GET /v1/models` mas funciona por nome)
- Conta: ULTRA. A rota exige PRO+; plano grátis dá 403
- A cota é a **mesma** do site e do app. Duas janelas: sessão (4 h) e semana

### Pegadinhas que já custaram tempo

- **`arguments` vem como STRING JSON**, não objeto — precisa de `json.loads`
- O turno do assistente com `tool_calls` volta **inteiro** pro histórico
- `role: "tool"` sempre com o `tool_call_id` que veio
- A rota é **sem estado** — o histórico inteiro vai junto em toda chamada
- **Turno de assistente vazio envenena o histórico** — descartar, nunca reenviar
- Teto de voltas no laço (20). Agente sem teto é conta aberta
- `429` vai inteiro pro usuário — a mensagem já vem escrita pra gente ler
- Conferir o modelo pelo campo `model` da **resposta**, não do pedido

### Custo observado

~5.250 tokens de entrada na primeira mensagem (eram ~1.800 com 8 ferramentas),
subindo conforme o histórico cresce. Quase tudo é instrução + os 8 esquemas de ferramenta, que vão
em toda chamada. Saída fica em 30–70 tokens. O histórico é aparado em 20 turnos
**porque a memória é o banco** — contexto longo aqui não compra nada.

---

## 7. WhatsApp — HuberChat via painel StarCore

### O caminho

O webhook do HuberChat é **por canal**, e o canal já atende os 10 grupos da
StarCore — não dá pra apontar ele pra cá sem derrubar o resto. Então:

```
HuberChat → painel StarCore (10.0.0.Z) → esta máquina 10.0.0.Y:8090/webhook
```

O painel repassa o payload **verbatim**, sem reserializar, assinado com o mesmo
esquema. **O código não sabe que o painel existe e não deve saber** — trata
como se viesse do HuberChat direto. Se um dia ligarem webhook por grupo no
HuberChat, é repontar a URL lá e aqui não muda uma linha.

O painel só repassa mensagem que começa com "Lauren". Mesmo assim aqui se
valida tudo de novo — defesa em profundidade.

A resposta **não volta pelo painel**: sai direto na API do HuberChat.

### O que chega — 9 campos no TOPO do JSON, nada aninhado

```
text       string   o texto da mensagem
from       string   número de quem mandou (ex: 5511999999999)
fromMe     bool     true = a própria Lauren mandou → IGNORA (senão vira laço)
isGroup    bool
groupId    string   o JID: 120363XXXXXXXXXXXX@g.us
groupName  string
pushName   string   nome que a pessoa usa no WhatsApp
messageId  string
type       string   'text'
```

Não existe `chatId`, `jid`, `body` nem `participant`. São esses 9.

### Assinatura

Cabeçalhos: `X-HuberChat-Signature: sha256=<hex>` e `X-HuberChat-Timestamp: <epoch>`

```python
esperada = hmac.new(segredo.encode(), f"{ts}.".encode() + corpo, hashlib.sha256).hexdigest()
```

- É sobre **`"<timestamp>." + corpo cru`**, NÃO sobre o corpo sozinho
- Chave é o segredo como **texto puro** (veio de `secrets.token_urlsafe(32)`).
  Nada de decodificar base64 — **uma leitura só**. Aceitar mais de uma não abre
  buraco, mas esconde qual é a certa, e no dia do erro não se sabe qual falhou
- **Janela de 5 minutos** (`JANELA = 300`): fora dela, recusa. É o que impede
  repetir pra sempre uma requisição capturada
- Comparação com `hmac.compare_digest`, sempre

### Como manda — NÃO é JSON

```
POST http://10.0.0.X:3000/api/group
Authorization: Basic base64("KEY:TOKEN")
Content-Type: application/x-www-form-urlencoded

groupId=<jid>&msg=<texto>
```

- `/api/group` pro grupo (param `groupId`); `/api/sms` pra individual (`phone`)
- O campo do texto chama **`msg`**, não `message`
- IP interno `10.0.0.X:3000`. **Não usar `app.huberchat.com`**
- Isolado em `montar_envio()` / `enviar_whatsapp()`: se o formato mudar, muda
  ali e nada mais no arquivo muda

### O grupo

`Nome do Grupo` → `120363XXXXXXXXXXXX@g.us`
Qualquer outro JID é ignorado **em silêncio**.

---

## 8. Segurança — não-negociável

1. **Zero comando de rede.** Nada de `/failover`, `/rota`, `/auto`. Isso vive
   só no grupo STARCORE. Esta máquina nem tem SSH pros MikroTiks
2. **Um grupo só.** JID diferente é ignorado em silêncio. Privado também
3. **`config.json` e `casa.db` em chmod 600**
4. **Assinatura validada sempre** — sem assinatura válida, descarta
5. **Isolada do painel StarCore.** São as contas da casa, não da empresa
6. **Porta não exposta na internet.** Sem domínio, sem SSL, sem proxy — o
   webhook nunca sai da rede interna. Escuta em `0.0.0.0:8090` só pra aceitar
   do painel; a porta não é liberada no firewall de borda
7. **Wake word ligada** (`responder_sem_wake: false`) — ela só responde quando
   é chamada pelo nome. Não lê nem comenta a conversa do casal

A unit do systemd roda com `ProtectSystem=strict` e só enxerga
`/opt/lauren-assistente` pra escrita.

---

## 9. Estado atual (07/09/2026)

**No ar.** Passos 1 a 13 do checklist feitos.

```
lauren-assistente.service   active, enabled   LISTEN 0.0.0.0:8090
teste_ferramentas.py        25/25
teste_webhook.py            57/57
```

Ciclo completo provado no grupo, ponta a ponta:

```
aceita de 10.0.0.Z (371 bytes)
(o pushName do WhatsApp): Lauren, já comprei sabão
→ Marquei como comprado: sabão. Ainda falta cabeça de chuveiro. ['lista_marcar_comprado']
envio ok [200]
```

E no banco: `comprado = 1`, com carimbo de hora, e a linha continuando lá.
**Ela grava de verdade — não finge.** ~2,8 s de ponta a ponta.

### Pendências

- [x] ~~`pessoas` vazio~~ — resolvido em 07/09/2026 no `config.json`. Sem os dois números, o campo
      As chaves do `pessoas` **não são telefone: são LID do WhatsApp**
      (`ID-DA-PESSOA-1` = pessoa 1, `ID-DA-PESSOA-2` = pessoa 2), porque a
      privacidade de número está ligada no grupo. Se alguém entrar no grupo,
      o LID aparece no log (`INFO Nome [id]: mensagem`) e é só acrescentar.
- [ ] **Cron dos lembretes NÃO ligado** — é o passo 14, depois do 13 fechar:
      `0 6` (manhã) e `0 21 --noite` (cobrança do que ficou pendente)
- [ ] **Pares do aluguel ainda não testados no grupo** (no terminal passaram):
      "já foi pago?" → não; "paguei o aluguel" → `competencia` = mês atual;
      "já foi pago?" → sim
- [ ] **BUG ABERTO — `lista_ver` não expõe `incluir_comprados`.** Perguntaram
      "o que eu comprei esse mês"; a ferramenta devolveu só o pendente (a
      palavra "sabão" não aparecia no resultado) e ela respondeu "Você comprou
      este mês: sabão" **a partir do histórico da conversa, não do banco**.
      Acertou por sorte — a compra tinha 30 segundos. Com o histórico aparado
      em 20 turnos, a mesma pergunta viraria invenção com a mesma confiança.
      É o modo de falha que o projeto inteiro existe pra evitar.
      **Conserto (2 linhas, ambas no `cerebro.py`):** expor `incluir_comprados`
      no esquema do `lista_ver`, e pôr nas instruções que ela não afirma fato
      da casa que a ferramenta não devolveu

---

### Caixinhas (12/09/2026)

Caixinha do Nubank é um `banco` com `tipo='caixinha'`. Guardar e tirar é
`transferir`, que cria dois movimentos com categoria **`Transferência`** —
excluída do `resumo`, do `gastos_periodo` e do "quanto entrou". **Guardar R$500
não é gastar R$500**; sem essa exclusão o relatório do mês vira ficção.

Três regras que os testes travam:

- **Caixinha nunca é escolhida sozinha.** `_resolver_banco` sem nome só olha
  `tipo <> 'caixinha'`, senão um "uber 27" sairia da reserva de emergência.
- **Os dois lados do `transferir` são opcionais.** "Guardei 500 na viagem" não
  diz de onde sai; exigir os dois fazia o modelo inventar um banco chamado
  "conta". O lado omitido vira a conta principal.
- **`quanto_sobra` usa só o disponível**, e cita o guardado à parte. "Posso
  gastar quanto?" não conta a reserva.

Banco aposentado (`bancos.ativo = 0`) some das consultas mas mantém histórico.
Gastar num aposentado devolve erro explicando — antes estourava com
`UNIQUE constraint failed`, porque não achava (filtro por ativo) e tentava criar.

**Em 12/09/2026 o dono migrou tudo pro Nubank:** Inter e C6 aposentados, Nubank
começou zerado, as 4 parcelas futuras do iPhone movidas pra ele, e 4 caixinhas
criadas (IPVA, Viagem, Emergência, Casa). **Vai ser sempre uma conta só.**

### O saldo só conta o que já aconteceu (09/09/2026)

`_saldo()` filtra `quando <= hoje`. Sem isso, as 10 parcelas de uma compra
derrubariam o saldo hoje — e não é isso que acontece na vida. Também consertou
um bug latente: gasto lançado com data futura tirava dinheiro na hora.

### Aparar histórico sem partir ciclo de ferramenta (12/09/2026)

**`mensagens[-20:]` cru é bug.** O corte podia começar num `role: "tool"` cujo
turno de assistente (o que pediu a ferramenta) ficou pra trás, e a Lauren
recusa o pedido inteiro com 400 "o histórico de ferramenta está quebrado".
Em 200 conversas aleatórias com ferramenta, **31% quebravam.**

Use `cerebro.aparar_historico()`: corta e depois anda pra frente até achar um
`user`, porque é ali que um turno começa. `_limpar()` joga fora `tool` órfão
como segunda camada.

Por que demorou a aparecer: a fila de reprocessamento chama
`responder(mensagem, quem)` **sem histórico**, então a retentativa funcionava
sempre. A mensagem falhava, entrava na fila, e 5 minutos depois passava — o que
parecia "a Lauren está instável" era o meu corte quebrando e a fila salvando.

**Regra da Lauren, que vale separar:** `400` é erro do pedido — a mensagem diz
o que arrumar e repetir igual nunca passa, então NÃO vai pra fila. `502` é do
lado deles e merece nova tentativa. Antes eu tratava tudo igual.

Nunca use `n` como variável em teste: é o contador de verificações dos arquivos
`teste_*.py`. Sobrescrevi e o `teste_webhook` passou de 57 pra 33 "verificações"
sem nenhuma falha aparente.

### Rede de segurança (07/09/2026)

- **Fila de reprocessamento** (`fila`): se a API da Lauren cair, a mensagem é
  guardada e uma thread tenta de novo a cada 5 min, 12 vezes. **Só 401 (chave
  inválida) fica fora da fila.** Em 10/09/2026 a Lauren devolveu **403 "plano
  gratuito"** às 23:48 durante uma instabilidade e voltou a funcionar às 00:03
  sem ninguém mexer na conta — logo depois deu 502 "fora do ar". Um 403 dela
  NÃO prova que o plano mudou; conferir de novo em alguns minutos antes de
  mandar alguém mexer em cobrança. **Só entra na
  fila o que falhou ANTES de gravar qualquer coisa** — `ErroLauren` carrega
  `ferramentas_ja_rodadas`, e se alguma rodou, reprocessar gravaria em dobro.
- **Alerta de queda**: `OnFailure=lauren-alerta.service` + `StartLimitBurst=5`
  em 10 min. Ciclo de queda manda mensagem no grupo. O `alerta.py` não usa IA
  de propósito: se a máquina está em apuros, a mensagem não pode depender de
  uma chamada de rede pra um modelo.
- **Arquivamento**: `historico` com mais de 6 meses vai pra
  `arquivo/historico-ate-AAAA-MM-DD.json` (chmod 600) e sai da tabela.
- **BACKUP DO `casa.db`: NÃO EXISTE NESTA MÁQUINA.** Conferido em 07/09/2026 —
  nenhum cron, timer, share ou cópia. O dono disse que já fez; se for snapshot
  de VM, vale lembrar que ele copia o arquivo com escrita em andamento. O jeito
  seguro pra SQLite é `.backup`. **Confirmar isso antes de confiar.**

- **A máquina reinicia sozinha às vezes** (hospedagem). Em 10/09/2026 às 11:12
  ela reiniciou e o serviço voltou sozinho pelo `systemctl enable`, sem perder
  nada. O cron também sobreviveu.

### Decidido em 07/09/2026 — não mexer sem falar com o dono

**A confirmação repete o texto INTEIRO do que foi gravado.** O dono perguntou
se não estava grande demais e decidiu manter. Não "otimizar" isso depois: a
confirmação é o único momento em que se descobre que ela entendeu errado —
"porta velha" no lugar de "porta nova" só aparece ali. É o mesmo motivo do
`2300` que não pode virar `23,00`. O texto é grande porque o dono escreveu
grande; encurtar seria ela decidindo o que é dispensável.

**Formato: recibo é pra UM item; lista é uma linha por item.** A tela do
WhatsApp é estreita e texto longo quebra sozinho — item em duas linhas
(título numa, data noutra) vira um bloco ilegível. Data igual pra todos vai
uma vez no título. Linha em branco entre título e lista.

**Modelo fixo em `lauren-4`. NÃO trocar** (decidido em 12/09/2026). Naquele dia
`lauren-4`, `nebula-3` e `velix-5` oscilaram com 502 "A Lauren está fora do ar"
enquanto a `lauren-6` respondia sempre — mesma chave, mesma requisição, só o
campo `model` mudando. A tentação é trocar pra `lauren-6`; o dono decidiu que
não, porque ela consome bem mais cota e **a fila de reprocessamento dá conta**:
a mensagem que caiu às 11:25:52 entrou sozinha às 11:30:32, na primeira
retentativa. Instabilidade intermitente não justifica trocar de modelo.

### Decidido que NÃO vai ter (07/09/2026)

**Foto e áudio.** O dono decidiu: só texto. Não propor de novo, não construir.
A API da Lauren até enxerga imagem, e o `type` diferente de `text` já é
ignorado no `tratar()` — é só não mexer nisso.

---

## 10. Armadilhas já pisadas — não repetir

1. **Assinar só o corpo.** O esquema é `ts + "." + corpo`. Tem teste travando
   isso: se alguém "simplificar" de volta, o teste quebra
2. **Requisição aceita tem que deixar rastro no log.** Antes, só a recusa
   logava — então um webhook que funciona e um que nunca chegou eram
   indistinguíveis, e isso fez o handshake do dono ser lido ao contrário.
   Hoje toda aceitação escreve `aceita de <ip> (N bytes)`
3. **`teste_webhook.py` sobe na 8091, NUNCA na 8090.** Com o serviço no ar, a
   instância do teste não conseguia bindar e as requisições iam parar na
   produção. O teste aborta se achar alguém na 8091
4. **`fromMe: true` tem que ser ignorado.** Ela responde no grupo, o webhook
   recebe a própria resposta de volta, e o wake word está no nome dela — sem
   esse corte é laço infinito
5. **Responder 200 antes de pensar.** A Lauren leva ~3 s; quem espera reenvia.
   Responde na hora e processa numa thread, com dedup por `messageId`
6. **O cron pode estar em UTC mesmo com o sistema em -03.** O daemon lê o
   fuso quando sobe; se subiu antes do fuso estar definido, fica em UTC pra
   sempre e o `timedatectl` não denuncia. Em 08/09/2026 o aviso das 6h saiu
   às **3h da manhã**. Conserto: `CRON_TZ=America/Sao_Paulo` no topo do
   crontab (não depende da ordem de boot) + `systemctl restart cron`.
   **Sempre provar com um job de teste 2 minutos à frente** — não confiar em
   `timedatectl`
7. **Saída de job do cron sem redirecionamento é descartada** ("No MTA
   installed, discarding output"). Foi por isso que não deu pra saber se a
   mensagem das 3h chegou a ser enviada. Hoje vai pra `lembretes.log`
8. **Faltou ferramenta = ela improvisa e escreve dado falso.** Pediram pra
   renomear item da lista; sem `lista_corrigir`, ela marcou o errado como
   comprado e criou outro — gravou uma compra que não houve. Antes disso,
   sem caminho pra "o que já comprei", respondeu pelo histórico da conversa.
   **Toda operação de CRIAR precisa da de CORRIGIR junto.** Foi por isso que
   `estornar` nasceu com o resto do dinheiro, não depois
9. **Casar por substring solta pega item errado.** "x" casava com "levar o
   lixo pra rua" (por causa do "lixo") e "pá" com "papelão". O pedaço tem que
   ser **palavra inteira** (`\b`) e ter no mínimo 4 letras. "pá" achando "pá de
   lixo" continua certo — é palavra inteira e é o único item com ela.
10. **Casar item por frase inteira não funciona.** "fechadura da porta" não é
   substring de "fechadura porta" — o "da" quebra o LIKE. E comparar palavra
   por palavra sem tirar acento também não: "papelao" ≠ "papelão". O
   `_achar_item()` tenta igual → contém → mesmas palavras sem acento e sem
   "de/da/do". Soltar mais que isso casaria coisa errada
10. **Validar ANTES de inserir.** `banco_salvar` recusava saldo zero *depois*
   de já ter criado o banco: a resposta dizia "não foi possível cadastrar" e
   o banco estava lá. Mensagem e banco discordando é pior que os dois errados
11. **Com dinheiro, perguntar em vez de chutar.** "Adiciona 69,77 no Inter" é
   ambíguo (entrou ou saiu?). Ela pergunta e não grava nada até saber
12. **Pergunta dela tem que vir com a resposta pronta.** O painel só repassa
   mensagem que começa com "Lauren" — quem responder só "inter" não chega
   aqui e acha que foi ignorado. Ela escreve: *Responda assim: Lauren, foi do
   Inter*
13. **Ela não pode se chamar pelo próprio nome.** A instrução de mostrar a
   resposta pronta ("Responda assim: *Lauren, foi do Inter*") vazou pro início
   da fala dela — saiu "Lauren, por enquanto consigo guardar fatos...", como
   se chamasse a si mesma
14. **Ao dizer que não faz algo, não sugerir frase que pareça resolver.** Ela
   disse que não tinha lembrete e emendou "diga: Lauren, lembre que preciso
   revisar o carro" — que virava fato e nunca avisava. Prometer sem entregar é
   parente próximo do "marquei!" sem gravar
15. **Código não verificado não vale como passo concluído.** O `cerebro.py`
   ficou dias com um cabeçalho dizendo "ESTE ARQUIVO NUNCA RODOU" até o teste
   real passar. Cabeçalho de depuração e instrumentação temporária se apagam
   assim que servem

---

## 11. Como o dono trabalha

- **Ele confere por fora.** Roda os testes, lê o schema, lê o código. Entregar
  coisa não verificada custa caro
- **Parar nos pontos de parada.** "Se parecer que tem trabalho adiantável sem a
  dependência real, é sinal de que seria mock." Um cérebro falso faz os testes
  passarem e o relatório dizer "funcionando" quando o que existe é um boneco —
  é pior do que não ter feito, porque esconde o problema
- **Nada de valor inventado** em config, nem mock, nem stub, nem fallback
- **Pedir autorização antes de instalar pacote** — ele audita as máquinas
- **Relatar com a saída do SQL junto**, não só o que ela respondeu

---

## 12. Comandos do dia a dia

```bash
# ver o que está acontecendo
journalctl -u lauren-assistente -f
systemctl status lauren-assistente
curl http://10.0.0.Y:8090/saude          # → vivo

# conferir o banco na mão
sqlite3 -header -column casa.db "select * from contas;"
sqlite3 -header -column casa.db "select * from pagamentos;"
sqlite3 -header -column casa.db "select * from lista_compras;"
sqlite3 -header -column casa.db "select id,quem,mensagem,resposta,ferramentas from historico;"

# os testes (não tocam no casa.db — usam banco descartável)
python3 teste_ferramentas.py
python3 teste_webhook.py

# conversar sem WhatsApp, no mesmo banco
python3 assistente.py --terminal
#   dentro dele: !sql select * from lista_compras

# o aviso de conta, sem mandar nada
python3 lembretes.py --seco

# depois de mexer no config.json ou no código
systemctl restart lauren-assistente
```

### O teste que sempre vale

Dizer **"Lauren, já comprei sabão"**, ela responder "marquei", e o
`sqlite3 casa.db "select * from lista_compras"` mostrar `comprado = 1`.
Se mostrar `0`, ela está fingindo — e o problema é do lado da Lauren, não do
código daqui.

---

## 13. Backup da casa.db — quem faz o quê

Escrito em 07/09/2026 pelo Claude do painel StarCore.

### Por que isto existe

A `casa.db` é o **único** lugar onde as contas e a lista da casa existem. Ela
não reflete nada de outro sistema — diferente do painel StarCore, que espelha
o que está nos equipamentos. Se esta VM morrer, o dado morre com ela.

Por isso o backup é feito em **dois lugares independentes**:

| Onde | Quem faz | Quando | Guarda |
|---|---|---|---|
| Local, nesta máquina | você (agente daqui) | diário | 14 dias |
| Painel StarCore (10.0.0.Z) | o painel, sozinho | 03:30 | 30 dias |

### O que o painel faz — não precisa de nada seu

`starcoredc-backup-assistente.timer` roda todo dia às 03:30 e:

1. Entra por SSH e roda `sqlite3 casa.db ".backup /tmp/casa-snapshot-<pid>.db"`
2. Traz o snapshot pro painel via `scp`
3. Apaga o temporário daqui
4. **Abre o arquivo e confere** que as 5 tabelas existem antes de dar ok
5. Guarda em `/opt/starcoredc/backups-assistente/casa-AAAA-MM-DD.db` (chmod 600)

Script: `/opt/starcoredc/backup_assistente.sh` (no painel, não aqui).
Log: `/opt/starcoredc/logs/backup-assistente.log` (no painel).

**Três coisas que importam pra você:**

- Ele usa `.backup` do sqlite3, **não `cp`**. O banco está em uso por um serviço
  vivo e copiar o arquivo pode pegar uma escrita pela metade. Faz o mesmo no
  backup local daqui.
- Ele lê a senha SSH do `brains.json` do painel. Se a senha desta máquina
  mudar, atualiza lá — senão o backup para em silêncio.
- Ele cria e apaga `/tmp/casa-snapshot-*.db` de madrugada. Se você vir esse
  arquivo, é o backup rodando, não sujeira.

### O que ainda NÃO tem

O backup vive em duas máquinas, mas **as duas estão no mesmo prédio**. Não há
cópia fora do local. O painel tem rclone configurado para os backups dele, mas
a `casa.db` **não** foi mandada pra lá de propósito: é dado de família, e
mandar pra nuvem é decisão do dono, não default técnico. Se ele pedir, é uma
linha no script do painel.

### Como conferir que está funcionando

No painel: `tail /opt/starcoredc/logs/backup-assistente.log`
Espera-se: `ok: .../casa-AAAA-MM-DD.db (N bytes, N contas)`

Daqui não dá pra ver — o backup é puxado, não empurrado. Esta máquina não sabe
que ele acontece, e isso é de propósito: se ela for comprometida, não tem como
alcançar nem apagar as cópias.
