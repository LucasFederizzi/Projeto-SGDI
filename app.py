from flask import Flask, render_template, request, redirect, url_for, flash
import sqlite3
import re
import string
from datetime import datetime, date

app = Flask(__name__)
app.secret_key = '123456'

def get_db():
    conn = sqlite3.connect('demandas.db')
    conn.row_factory = sqlite3.Row
    return conn

def validar_data_prazo(data_str):
    if not data_str:
        return False, "O prazo é obrigatório."
    
    try:
        data_prazo = datetime.strptime(data_str, '%Y-%m-%d').date()
        data_atual = date.today()

        if data_prazo < data_atual:
            return False, "O prazo não pode ser uma data no passado."
        
        return True, ""
    
    except ValueError:
        return False, "Data inválida."

def caracteres_invalidos(*textos): 
    for texto in textos:
        if texto and any(char in string.punctuation for char in texto):
            return True
    return False

def validar_limites_caracteres(titulo, descricao, solicitante):
    """Valida os limites máximos de caracteres"""
    limites = {
        'titulo': (100, 'Título'),
        'descricao': (1000, 'Descrição'),
        'solicitante': (100, 'Solicitante')
    }
    
    if titulo and len(titulo) > limites['titulo'][0]:
        return False, f"{limites['titulo'][1]} não pode exceder {limites['titulo'][0]} caracteres (você digitou {len(titulo)})"
    
    if descricao and len(descricao) > limites['descricao'][0]:
        return False, f"{limites['descricao'][1]} não pode exceder {limites['descricao'][0]} caracteres (você digitou {len(descricao)})"
    
    if solicitante and len(solicitante) > limites['solicitante'][0]:
        return False, f"{limites['solicitante'][1]} não pode exceder {limites['solicitante'][0]} caracteres (você digitou {len(solicitante)})"
    
    return True, ""


@app.route('/')
def index():
    conn = get_db()
    cursor = conn.cursor()
    query = """
        SELECT * FROM demandas 
        ORDER BY 
            CASE prioridade 
                WHEN 'Urgente' THEN 1 
                WHEN 'Alta' THEN 2 
                WHEN 'Média' THEN 3 
                WHEN 'Baixa' THEN 4 
                ELSE 5 
            END,
            prazo ASC
    """

    demandas = cursor.execute(query).fetchall()
    conn.close()
    return render_template('index.html', demandas=demandas)


@app.route('/nova_demanda', methods=['GET', 'POST'])
def nova_demanda():
    if request.method == 'POST':
        titulo = request.form['titulo']
        descricao = request.form['descricao']
        solicitante = request.form['solicitante']
        prioridade = request.form['prioridade']
        prazo = request.form['prazo']

        # Executa a validação de data
        prazo_valido, msg_erro_prazo = validar_data_prazo(prazo)
        if not prazo_valido:
            flash(msg_erro_prazo)
            return redirect('/nova_demanda')

        # Valida limites de caracteres
        limites_ok, msg_erro_limites = validar_limites_caracteres(titulo, descricao, solicitante)
        if not limites_ok:
            flash(msg_erro_limites)
            return redirect('/nova_demanda')

        if caracteres_invalidos(titulo, descricao, solicitante, prioridade):
            flash('Os campos não podem conter caracteres especiais:')
            return redirect('/nova_demanda')

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO demandas (titulo, descricao, solicitante, data_criacao, prioridade, prazo) VALUES (?, ?, ?, ?, ?, ?)",
            (titulo, descricao, solicitante, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), prioridade, prazo)
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
        solicitante = request.form['solicitante']
        prioridade = request.form['prioridade']
        prazo = request.form['prazo']

        # Valida limites de caracteres
        limites_ok, msg_erro_limites = validar_limites_caracteres(titulo, descricao, solicitante)
        if not limites_ok:
            flash(msg_erro_limites)
            conn.close()
            return redirect(f'/editar/{id}')

        if caracteres_invalidos(titulo, descricao, solicitante, prioridade):
            flash('Os campos não podem conter caracteres especiais')
            conn.close()
            return redirect(f'/editar/{id}')

        cursor.execute(
            "UPDATE demandas SET titulo=?, descricao=?, solicitante=?, prioridade=?, prazo=? WHERE id=?",
            (titulo, descricao, solicitante, prioridade, prazo, id)
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


@app.route('/buscar')
def buscar():
    termo = request.args.get('q', '').strip()

    if caracteres_invalidos(termo):
        flash('A busca não pode conter os caracteres especiais.')
        return redirect('/')

    conn = get_db()
    cursor = conn.cursor()

    query = """
        SELECT * FROM demandas 
        WHERE titulo LIKE ? 
        ORDER BY 
            CASE prioridade 
                WHEN 'Urgente' THEN 1 
                WHEN 'Alta' THEN 2 
                WHEN 'Média' THEN 3 
                WHEN 'Baixa' THEN 4 
                ELSE 5 
            END,
            prazo ASC,
            CASE 
                WHEN LOWER(titulo) = LOWER(?) THEN 1
                WHEN LOWER(titulo) LIKE LOWER(?) THEN 2
                ELSE 3
            END,
            titulo ASC
    """
    
    resultados = cursor.execute(
        query, 
        (f'%{termo}%', termo, f'{termo}%')
    ).fetchall()
    
    conn.close()
    return render_template('index.html', demandas=resultados)


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
    # .strip() remove espaços em branco antes e depois do texto
    comentario = request.form['comentario'].strip()
    autor = request.form['autor'].strip()

    # Validação para verificar se os campos estão vazios
    if not comentario or not autor:
        flash('O comentário e o autor não podem estar vazios!')
        return redirect(f'/detalhes/{demanda_id}')

    # Validação de limites de caracteres para comentários
    if len(autor) > 100:
        flash(f'Nome do autor não pode exceder 100 caracteres (você digitou {len(autor)})')
        return redirect(f'/detalhes/{demanda_id}')
    
    if len(comentario) > 1000:
        flash(f'Comentário não pode exceder 1000 caracteres (você digitou {len(comentario)})')
        return redirect(f'/detalhes/{demanda_id}')

    # Validação dos caracteres proibidos
    if caracteres_invalidos(comentario, autor):
        flash('Os campos não podem conter caracteres especiais')
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


def calcular_prazo(data_inicio):
    return "30 dias"


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')