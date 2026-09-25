from flask import Flask, render_template, request, redirect, url_for, flash, session
import sqlite3
import string
from datetime import datetime

app = Flask(__name__)
app.secret_key = '123456'


def get_db():
    conn = sqlite3.connect('demandas.db')
    conn.row_factory = sqlite3.Row
    return conn


def caracteres_invalidos(*textos): 
    for texto in textos:
        if texto and any(char in string.punctuation for char in texto):
            return True
    return False

@app.before_request
def verificar_login():
    if 'email' not in session and request.endpoint not in ['login', 'logout', 'static']:
        return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        senha = request.form.get('senha', '')

        if not email or not senha:
            flash('Por favor, preencha o e-mail e a senha.')
            return redirect(url_for('login'))

        # Consulta no banco de dados na tabela de solicitantes
        conn = get_db()
        cursor = conn.cursor()
        usuario = cursor.execute(
            "SELECT * FROM solicitantes WHERE LOWER(email) = ? AND senha = ?",
            (email, senha)
        ).fetchone()
        conn.close()

        if usuario:
            session['usuario_id'] = usuario['id']
            session['email'] = usuario['email']
            session['usuario'] = usuario['nome']  
            return redirect(url_for('index'))
        else:
            flash('E-mail ou senha incorretos.')
            return redirect(url_for('login'))

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()  
    flash('Você saiu da sua conta.')
    return redirect(url_for('login'))


ORDENACOES = {
    'solicitante': "LOWER(solicitante) ASC",
    'data': "data_criacao DESC",
    'prioridade': """
        CASE prioridade 
            WHEN 'Urgente' THEN 1 
            WHEN 'Alta' THEN 2 
            WHEN 'Média' THEN 3 
            WHEN 'Baixa' THEN 4 
            ELSE 5 
        END,
        prazo ASC
    """,
    'prazo': "prazo ASC",
}


@app.route('/')
def index():
    tipo_busca = request.args.get('tipo_busca', '')
    termo = request.args.get('termo', '').strip()
    ordenar = request.args.get('ordenar', 'prioridade')

    if ordenar not in ORDENACOES:
        ordenar = 'prioridade'

    condicao = '1=1'
    parametros = []

    if tipo_busca in ('id_demanda', 'id_solicitante', 'titulo', 'solicitante'):
        if not termo:
            flash('Digite um termo para buscar.')
            tipo_busca = ''
        elif caracteres_invalidos(termo):
            flash('A busca não pode conter caracteres especiais.')
            tipo_busca = ''
        elif tipo_busca == 'id_demanda':
            condicao, parametros = "CAST(id AS TEXT) LIKE ?", [f'{termo}%']
        elif tipo_busca == 'id_solicitante':
            condicao, parametros = "CAST(solicitante_id AS TEXT) = ?", [termo]
        elif tipo_busca == 'titulo':
            condicao, parametros = "LOWER(titulo) LIKE LOWER(?)", [f'%{termo}%']
        else:  # solicitante (busca por nome)
            condicao, parametros = "LOWER(solicitante) LIKE LOWER(?)", [f'%{termo}%']
    else:
        tipo_busca = ''

    conn = get_db()
    cursor = conn.cursor()
    query = f"SELECT * FROM demandas WHERE {condicao} ORDER BY {ORDENACOES[ordenar]}"
    demandas = cursor.execute(query, parametros).fetchall()
    conn.close()

    return render_template(
        'index.html', demandas=demandas,
        ordenar=ordenar, tipo_busca=tipo_busca, termo=termo
    )


@app.route('/nova_demanda', methods=['GET', 'POST'])
def nova_demanda():
    if request.method == 'POST':
        titulo = request.form['titulo']
        descricao = request.form['descricao']
        solicitante = session['usuario']
        solicitante_id = session['usuario_id']
        prioridade = request.form['prioridade']
        prazo = request.form['prazo']

        if caracteres_invalidos(titulo, prioridade):
            flash('Os campos (exceto descrição) não podem conter caracteres especiais.')
            return redirect('/nova_demanda')

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO demandas (titulo, descricao, solicitante, solicitante_id, data_criacao, prioridade, prazo) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (titulo, descricao, solicitante, solicitante_id, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), prioridade, prazo)
        )
        conn.commit()
        conn.close()

        flash('Salvo!')
        return redirect('/')

    return render_template('nova_demanda.html')


@app.route('/editar/<id>', methods=['GET', 'POST'])
def editar(id):
    conn = get_db()
    cursor = conn.cursor()

    if request.method == 'POST':
        titulo = request.form['titulo']
        descricao = request.form['descricao']
        prioridade = request.form['prioridade']
        prazo = request.form['prazo']

        if caracteres_invalidos(titulo, prioridade):
            flash('Os campos (exceto descrição) não podem conter caracteres especiais.')
            conn.close()
            return redirect(f'/editar/{id}')

        cursor.execute(
            "UPDATE demandas SET titulo=?, descricao=?, prioridade=?, prazo=? WHERE id=?",
            (titulo, descricao, prioridade, prazo, id)
        )
        conn.commit()
        conn.close()
        return redirect('/')

    demanda = cursor.execute('SELECT * FROM demandas WHERE id=?', (id,)).fetchone()
    conn.close()
    return render_template('editar.html', demanda=demanda)


@app.route('/deletar/<id>')
def deletar(id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM demandas WHERE id=?', (id,))
    conn.commit()
    conn.close()
    flash('Deletado!')
    return redirect('/')


@app.route('/detalhes/<id>')
def detalhes(id):
    conn = get_db()
    cursor = conn.cursor()
    demanda = cursor.execute('SELECT * FROM demandas WHERE id=?', (id,)).fetchone()
    comentarios = cursor.execute('SELECT * FROM comentarios WHERE demanda_id=?', (id,)).fetchall()
    conn.close()

    return render_template('detalhes.html', demanda=demanda, comentarios=comentarios)


@app.route('/adicionar_comentario/<demanda_id>', methods=['POST'])
def adicionar_comentario(demanda_id):
    comentario = request.form['comentario'].strip()
    autor = f"{session['usuario']} (#{session['usuario_id']})"
   
    if not comentario:
        flash('O comentário não pode estar vazio!')
        return redirect(f'/detalhes/{demanda_id}')

    if caracteres_invalidos(comentario):
        flash('O comentário não pode conter caracteres especiais')
        return redirect(f'/detalhes/{demanda_id}')

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO comentarios (demanda_id, comentario, autor, data) VALUES (?, ?, ?, ?)",
        (demanda_id, comentario, autor, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    )
    conn.commit()
    conn.close()

    return redirect(f'/detalhes/{demanda_id}')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')