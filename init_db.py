import sqlite3

conn = sqlite3.connect('demandas.db')
cursor = conn.cursor()

# SE A TABELA JÁ EXISTIR EXCLUI ELA E CRIA UM NOVA PARA NÃO DUPLICAS OS DADOS:

cursor.execute("DROP TABLE IF EXISTS solicitantes")
cursor.execute("DROP TABLE IF EXISTS comentarios")
cursor.execute("DROP TABLE IF EXISTS demandas")

# CRIAÇÃO DAS TABELAS:

cursor.execute('''
CREATE TABLE IF NOT EXISTS solicitantes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    senha TEXT NOT NULL
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
    FOREIGN KEY (solicitante_id) REFERENCES solicitantes (id)
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


# DADOS INSERIDOS:

cursor.execute("SELECT COUNT(*) FROM solicitantes")
if cursor.fetchone()[0] == 0:
    solicitantes_iniciais = [
        ('Juliana Castanho Teixeira', 'juliana@gmail.com', 'juliana'),
        ('Lucas boeira', 'lucas@gmail.com', 'lucas'),
        ('Gabriel Techio', 'techio@gmail.com', 'techio'),
        ('João Silva', 'joãosilva@gmail.com', 'joao'),
        ('Maria Santos', 'mariasantos@gmail.com', 'maria'),
        ('Pedro Costa', 'pedrocosta@gmail.com', 'pedro'),
        ('Ana Lima', 'analima@gmail.com', 'ana')
    ]
    cursor.executemany(
        "INSERT INTO solicitantes (nome, email, senha) VALUES (?, ?, ?)",
        solicitantes_iniciais
    )


cursor.execute("""
INSERT INTO demandas (id, titulo, descricao, solicitante, solicitante_id, data_criacao, prioridade, prazo) 
VALUES (1, 'Corrigir bug no login', 'Usuários não conseguem fazer login', 'João Silva', 4, '2024-01-15 10:30:00', 'Urgente', '2026-08-01')
""")

cursor.execute("""
INSERT INTO demandas (id, titulo, descricao, solicitante, solicitante_id, data_criacao, prioridade, prazo) 
VALUES (2, 'Implementar relatório de vendas', 'Precisamos de um relatório mensal', 'Maria Santos', 5, '2024-01-16 14:20:00', 'Alta', '2026-08-10')
""")

cursor.execute("""
INSERT INTO demandas (id, titulo, descricao, solicitante, solicitante_id, data_criacao, prioridade, prazo) 
VALUES (3, 'Melhorar performance', 'Sistema está lento', 'Pedro Costa', 6, '2024-01-17 09:15:00', 'Média', '2026-08-15')
""")

cursor.execute("""
INSERT INTO demandas (id, titulo, descricao, solicitante, solicitante_id, data_criacao, prioridade, prazo) 
VALUES (4, 'Adicionar filtros', 'Usuários querem filtrar demandas', 'Ana Lima', 7, '2024-01-18 11:00:00', 'Baixa', '2026-08-20')
""")

cursor.executemany(
    """
    INSERT INTO demandas
        (id, titulo, descricao, solicitante, solicitante_id, data_criacao, prioridade, prazo)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """,
    [
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

cursor.execute("""
INSERT INTO comentarios (id, demanda_id, comentario, autor, data) 
VALUES (1, 1, 'Vou investigar esse bug', 'Tech Team', '2024-01-15 11:00:00')
""")

cursor.execute("""
INSERT INTO comentarios (id, demanda_id, comentario, autor, data) 
VALUES (2, 1, 'Bug corrigido na branch develop', 'Desenvolvedor', '2024-01-15 16:30:00')
""")

cursor.execute("""
INSERT INTO comentarios (id, demanda_id, comentario, autor, data) 
VALUES (3, 99, 'Este comentário está órfão', 'Usuário', '2024-01-16 10:00:00')
""")

conn.commit()
conn.close()

print("Banco de dados criado com sucesso!")