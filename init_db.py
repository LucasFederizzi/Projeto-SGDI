import sqlite3
from datetime import datetime

from werkzeug.security import generate_password_hash

conn = sqlite3.connect('demandas.db')
cursor = conn.cursor()

# ATENÇÃO: este script RECRIA solicitantes, demandas e comentários (dados de teste).
# A tabela de LOGS NUNCA é apagada: ela é a base histórica de auditoria.

cursor.execute("DROP TABLE IF EXISTS solicitantes")
cursor.execute("DROP TABLE IF EXISTS comentarios")
cursor.execute("DROP TABLE IF EXISTS demandas")

# CRIAÇÃO DAS TABELAS:

cursor.execute('''
CREATE TABLE solicitantes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    senha TEXT NOT NULL,                      -- hash da senha (nunca texto puro)
    perfil TEXT NOT NULL DEFAULT 'usuario'    -- 'usuario' ou 'auditor' (pode ver /logs)
)
''')

cursor.execute('''
CREATE TABLE demandas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    titulo TEXT NOT NULL,
    descricao TEXT,
    solicitante TEXT,
    solicitante_id INTEGER,
    data_criacao TEXT,
    prioridade TEXT,
    prazo TEXT,
    status TEXT NOT NULL DEFAULT 'Aberto',
    responsavel TEXT,
    responsavel_id INTEGER,
    data_conclusao TEXT,
    FOREIGN KEY (solicitante_id) REFERENCES solicitantes (id),
    FOREIGN KEY (responsavel_id) REFERENCES solicitantes (id)
)
''')

cursor.execute('''
CREATE TABLE comentarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    demanda_id INTEGER,
    comentario TEXT,
    autor TEXT,
    data TEXT,
    FOREIGN KEY (demanda_id) REFERENCES demandas (id)
)
''')

# TABELA DE LOGS DE AUDITORIA (não é recriada: só ganha novos registros)
cursor.execute('''
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    data_hora TEXT NOT NULL,
    usuario_id INTEGER,
    usuario_nome TEXT,
    acao TEXT NOT NULL,
    entidade TEXT NOT NULL,
    entidade_id INTEGER,
    detalhes TEXT,
    ip TEXT
)
''')


# DADOS INSERIDOS:
# (nome, email, senha, perfil) - as senhas são gravadas como hash.
# Ajuste quais usuários são 'auditor' conforme o seu time.
solicitantes_iniciais = [
    ('Juliana Castanho Teixeira', 'juliana@gmail.com', 'juliana', 'auditor'),
    ('Lucas boeira', 'lucas@gmail.com', 'lucas', 'auditor'),
    ('Gabriel Techio', 'techio@gmail.com', 'techio', 'auditor'),
    ('João Silva', 'joaosilva@gmail.com', 'joao', 'usuario'),   # e-mail sem acento (type="email" não aceita)
    ('Maria Santos', 'mariasantos@gmail.com', 'maria', 'usuario'),
    ('Pedro Costa', 'pedrocosta@gmail.com', 'pedro', 'usuario'),
    ('Ana Lima', 'analima@gmail.com', 'ana', 'usuario'),
]
cursor.executemany(
    "INSERT INTO solicitantes (nome, email, senha, perfil) VALUES (?, ?, ?, ?)",
    [(n, e, generate_password_hash(s), p) for n, e, s, p in solicitantes_iniciais]
)

cursor.executemany(
    """
    INSERT INTO demandas
        (id, titulo, descricao, solicitante, solicitante_id, data_criacao, prioridade, prazo)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """,
    [
        (1, 'Corrigir bug no login', 'Usuários não conseguem fazer login', 'João Silva', 4, '2024-01-15 10:30:00', 'Urgente', '2026-08-01'),
        (2, 'Implementar relatório de vendas', 'Precisamos de um relatório mensal', 'Maria Santos', 5, '2024-01-16 14:20:00', 'Alta', '2026-08-10'),
        (3, 'Melhorar performance', 'Sistema está lento', 'Pedro Costa', 6, '2024-01-17 09:15:00', 'Média', '2026-08-15'),
        (4, 'Adicionar filtros', 'Usuários querem filtrar demandas', 'Ana Lima', 7, '2024-01-18 11:00:00', 'Baixa', '2026-08-20'),
        (5, 'Atualizar cadastro de clientes', 'Campos do cadastro precisam de revisao', 'Juliana Castanho Teixeira', 1, '2024-01-19 09:30:00', 'Alta', '2026-10-05'),
        (6, 'Corrigir envio de notificacoes', 'Notificacoes nao chegam para alguns usuarios', 'Lucas boeira', 2, '2024-01-20 10:15:00', 'Urgente', '2026-10-02'),
        (7, 'Criar painel de indicadores', 'Exibir os principais indicadores da equipe', 'Gabriel Techio', 3, '2024-01-21 13:40:00', 'Média', '2026-10-12'),
        (8, 'Revisar permissao de acesso', 'Ajustar acesso dos usuarios aos documentos', 'João Silva', 4, '2024-01-22 08:50:00', 'Alta', '2026-10-08'),
        (9, 'Organizar documentos internos', 'Separar documentos por departamento', 'Maria Santos', 5, '2024-01-23 14:10:00', 'Baixa', '2026-10-20'),
        (10, 'Corrigir exportacao de dados', 'Arquivo exportado apresenta campos incompletos', 'Pedro Costa', 6, '2024-01-24 11:25:00', 'Urgente', '2026-10-03'),
        (11, 'Melhorar tela de atendimento', 'Facilitar a consulta dos chamados abertos', 'Ana Lima', 7, '2024-01-25 15:00:00', 'Média', '2026-10-15'),
        (12, 'Adicionar campo de observacoes', 'Permitir registrar observacoes nas solicitacoes', 'Juliana Castanho Teixeira', 1, '2024-01-26 09:05:00', 'Baixa', '2026-10-22'),
        (13, 'Ajustar relatorio mensal', 'Incluir totais e filtros no relatorio mensal', 'Lucas boeira', 2, '2024-01-27 16:20:00', 'Alta', '2026-10-09'),
        (14, 'Corrigir validacao de formulario', 'Formulario aceita dados em formato incorreto', 'Gabriel Techio', 3, '2024-01-28 10:45:00', 'Urgente', '2026-10-04'),
        (15, 'Atualizar lista de departamentos', 'Incluir os departamentos mais recentes', 'João Silva', 4, '2024-01-29 12:00:00', 'Baixa', '2026-10-25'),
    ],
)

# Exemplos de status diferentes (as demais ficam 'Aberto'; as com prazo vencido
# aparecem como "Atrasado" automaticamente).
cursor.execute(
    "UPDATE demandas SET status = 'Concluído', responsavel = ?, responsavel_id = ?, "
    "data_conclusao = '2024-01-15 16:30:00' WHERE id = 1",
    ('João Silva', 4)
)
cursor.execute("UPDATE demandas SET status = 'Cancelado' WHERE id = 9")

cursor.executemany(
    "INSERT INTO comentarios (id, demanda_id, comentario, autor, data) VALUES (?, ?, ?, ?, ?)",
    [
        (1, 1, 'Vou investigar esse bug', 'Tech Team', '2024-01-15 11:00:00'),
        (2, 1, 'Bug corrigido na branch develop', 'Desenvolvedor', '2024-01-15 16:30:00'),
    ],
)

# Marca no histórico que os dados de teste foram recriados (os IDs recomeçam)
cursor.execute(
    "INSERT INTO logs (data_hora, usuario_id, usuario_nome, acao, entidade, entidade_id, detalhes, ip) "
    "VALUES (?, NULL, 'sistema', 'INICIALIZAR_BANCO', 'sistema', NULL, ?, NULL)",
    (datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
     'Dados de teste recriados via init_db.py (IDs de demandas/comentarios reiniciados)')
)

conn.commit()
conn.close()

print("Banco de dados criado com sucesso!")
