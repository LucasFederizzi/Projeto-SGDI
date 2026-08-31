import sqlite3

conn = sqlite3.connect('demandas.db')
cursor = conn.cursor()

cursor.execute("DROP TABLE IF EXISTS comentarios")
cursor.execute("DROP TABLE IF EXISTS demandas")

cursor.execute('''
CREATE TABLE demandas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    titulo TEXT NOT NULL,
    descricao TEXT,
    solicitante TEXT,
    data_criacao TEXT,
    prioridade TEXT,
    prazo TEXT
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


cursor.execute("""
INSERT INTO demandas (id, titulo, descricao, solicitante, data_criacao, prioridade, prazo) 
VALUES (1, 'Corrigir bug no login', 'Usuários não conseguem fazer login', 'João Silva', '2024-01-15 10:30:00', 'Urgente', '2026-08-01')
""")

cursor.execute("""
INSERT INTO demandas (id, titulo, descricao, solicitante, data_criacao, prioridade, prazo) 
VALUES (2, 'Implementar relatório de vendas', 'Precisamos de um relatório mensal', 'Maria Santos', '2024-01-16 14:20:00', 'Alta', '2026-08-10')
""")

cursor.execute("""
INSERT INTO demandas (id, titulo, descricao, solicitante, data_criacao, prioridade, prazo) 
VALUES (3, 'Melhorar performance', 'Sistema está lento', 'Pedro Costa', '2024-01-17 09:15:00', 'Média', '2026-08-15')
""")

cursor.execute("""
INSERT INTO demandas (id, titulo, descricao, solicitante, data_criacao, prioridade, prazo) 
VALUES (4, 'Adicionar filtros', 'Usuários querem filtrar demandas', 'Ana Lima', '2024-01-18 11:00:00', 'Baixa', '2026-08-20')
""")

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