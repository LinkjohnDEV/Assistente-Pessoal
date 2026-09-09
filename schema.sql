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
    onde          TEXT,                   -- mercado | casa | farmácia... (opcional)
    valor         REAL,                   -- preço estimado (opcional)
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

-- ─────────────────────────────────────────────────────────────────────────
-- DINHEIRO (acrescentado em 07/09/2026)
-- Nada acima mudou. Estas três tabelas são novas.
-- ─────────────────────────────────────────────────────────────────────────

-- Onde o dinheiro está: conta, cartão ou o dinheiro do bolso
CREATE TABLE IF NOT EXISTS bancos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nome        TEXT NOT NULL UNIQUE,
    tipo        TEXT DEFAULT 'conta',   -- conta | cartao | dinheiro
    criado_em   TEXT,
    criado_por  TEXT
);

-- O SALDO NÃO É COLUNA. É soma disto aqui.
--   SELECT SUM(valor) FROM movimentos WHERE banco_id = ? AND estornado = 0
-- Guardar saldo num campo dessincroniza no primeiro erro e apaga a resposta
-- de "onde foi meu dinheiro?" — que é metade da graça de anotar gasto.
CREATE TABLE IF NOT EXISTS movimentos (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    banco_id       INTEGER NOT NULL REFERENCES bancos(id),
    valor          REAL NOT NULL,          -- negativo = saída, positivo = entrada
    descricao      TEXT,
    categoria      TEXT,
    quando         TEXT NOT NULL,          -- 'AAAA-MM-DD': a data do GASTO
    competencia    TEXT NOT NULL,          -- 'AAAA-MM', derivado de quando
    registrado_em  TEXT,                   -- quando foi DIGITADO (outra coisa)
    registrado_por TEXT,
    estornado      INTEGER DEFAULT 0,      -- nunca apaga, só estorna
    estorno_de     INTEGER REFERENCES movimentos(id),
    -- Parcelamento (09/09/2026): "geladeira em 10x de 300" vira 10 linhas,
    -- uma por mês. As futuras só entram no saldo quando o mês chega.
    parcela        INTEGER,                -- 3 (de 10)
    parcelas       INTEGER,                -- 10
    grupo          TEXT                    -- liga as 10 entre si
);

-- Teto de gasto por categoria, por mês
CREATE TABLE IF NOT EXISTS limites (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    categoria  TEXT NOT NULL UNIQUE,
    valor_mes  REAL NOT NULL,
    criado_em  TEXT,
    criado_por TEXT
);

CREATE INDEX IF NOT EXISTS idx_mov_banco ON movimentos(banco_id);
CREATE INDEX IF NOT EXISTS idx_mov_comp  ON movimentos(competencia);
CREATE INDEX IF NOT EXISTS idx_mov_cat   ON movimentos(categoria);

-- ─────────────────────────────────────────────────────────────────────────
-- TAREFAS (acrescentado em 07/09/2026)
-- O meio-termo entre 'fatos' (guarda e nunca avisa) e 'contas' (só conta que
-- se paga todo mês): coisa que acontece uma vez, tem data, e alguém precisa
-- ser lembrado. Sem data também vale — vira só um "tenho que fazer".
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tarefas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tarefa      TEXT NOT NULL,
    quando      TEXT,                   -- 'AAAA-MM-DD' ou NULL (sem prazo)
    de_quem     TEXT,                   -- de quem é a tarefa, se disserem
    criado_em   TEXT,
    criado_por  TEXT,
    feita       INTEGER DEFAULT 0,      -- nunca apaga, igual à lista
    feita_em    TEXT,
    feita_por   TEXT,
    -- Recorrência (09/09/2026): quando uma recorrente é marcada feita, nasce
    -- a próxima. A linha antiga fica como histórico do que foi cumprido.
    repete      TEXT,                   -- diaria | semanal | mensal | NULL
    repete_de   INTEGER                 -- id da tarefa que gerou esta
);

CREATE INDEX IF NOT EXISTS idx_tar_feita  ON tarefas(feita);
CREATE INDEX IF NOT EXISTS idx_tar_quando ON tarefas(quando);

-- Mensagens que não puderam ser respondidas porque a API da Lauren falhou.
-- Sem isto, um 502 às 3h faz um gasto se perder em silêncio.
CREATE TABLE IF NOT EXISTS fila (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    quem             TEXT,
    mensagem         TEXT NOT NULL,
    criado_em        TEXT,
    tentativas       INTEGER DEFAULT 0,
    ultima_tentativa TEXT,
    ultimo_erro      TEXT
);
