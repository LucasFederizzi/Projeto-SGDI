import csv
import io
import os
import sqlite3
import string
from datetime import datetime

from flask import (Flask, render_template, request, redirect, url_for, flash,
                   session, jsonify, Response)
from werkzeug.security import check_password_hash

app = Flask(__name__)
# Em produção defina a variável de ambiente SECRET_KEY
app.secret_key = os.environ.get('SECRET_KEY', 'dev-only-troque-em-producao')

STATUS_FINAIS = ('Concluído', 'Cancelado')

# Ações que podem aparecer na tela de logs (também usadas no filtro)
ACOES_LOG = (
    'LOGIN', 'LOGIN_FALHA', 'LOGOUT',
    'CRIAR_DEMANDA', 'EDITAR_DEMANDA', 'EXCLUIR_DEMANDA',
    'ASSUMIR_RESPONSABILIDADE', 'CONCLUIR_DEMANDA', 'CANCELAR_DEMANDA',
    'CRIAR_COMENTARIO', 'INICIALIZAR_BANCO', 'EXPORTAR_RELATORIO',
)

# ---- Dashboard gerencial ----
NOME_EMPRESA = os.environ.get('NOME_EMPRESA', 'SGDI - Sistema de Gestão de Demandas')
PRIORIDADES = ('Urgente', 'Alta', 'Média', 'Baixa')
STATUS_EXIBIDOS = ('Aberto', 'Atrasado', 'Concluído', 'Cancelado')
PRIORIDADE_CRITICA = 'Urgente'   # "crítica" = prioridade Urgente
PESO_CRITICA = 2                  # críticas pesam mais na média de tempo (demais = 1)

# Atrasada = aberta com prazo vencido (usa a data LOCAL, não UTC)
STATUS_EXIBIDO_SQL = (
    "CASE WHEN status = 'Aberto' AND prazo < date('now', 'localtime') "
    "THEN 'Atrasado' ELSE status END"
)
BASE_DEMANDAS = f"(SELECT *, {STATUS_EXIBIDO_SQL} AS status_exibido FROM demandas)"


def get_db():
    conn = sqlite3.connect('demandas.db')
    conn.row_factory = sqlite3.Row
    return conn


def caracteres_invalidos(*textos):
    for texto in textos:
        if texto and any(char in string.punctuation for char in texto):
            return True
    return False


def agora():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def resumo(valor, limite=80):
    """Encurta textos longos para o log não ficar gigante."""
    texto = '' if valor is None else str(valor)
    return texto if len(texto) <= limite else texto[:limite] + '...'


def registrar_log(cursor, acao, entidade, entidade_id, detalhes=None,
                  usuario_id=None, usuario_nome=None):
    """
    Grava um registro de auditoria usando o MESMO cursor da operação: o commit
    da rota grava a ação e o log juntos (ou nenhum dos dois).
    Se usuario_id/usuario_nome não forem informados, usa o usuário da sessão.
    """
    cursor.execute(
        """INSERT INTO logs (data_hora, usuario_id, usuario_nome, acao,
                             entidade, entidade_id, detalhes, ip)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            agora(),
            usuario_id if usuario_id is not None else session.get('usuario_id'),
            usuario_nome if usuario_nome is not None else session.get('usuario'),
            acao, entidade, entidade_id, detalhes, request.remote_addr,
        )
    )


@app.before_request
def verificar_login():
    if 'email' not in session and request.endpoint not in ['login', 'logout', 'static']:
        return redirect(url_for('login'))


# ---------------------------------------------------------------- LOGIN / LOGOUT

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        senha = request.form.get('senha', '')

        if not email or not senha:
            flash('Por favor, preencha o e-mail e a senha.')
            return redirect(url_for('login'))

        conn = get_db()
        cursor = conn.cursor()
        usuario = cursor.execute(
            "SELECT * FROM solicitantes WHERE LOWER(email) = ?", (email,)
        ).fetchone()

        if usuario and check_password_hash(usuario['senha'], senha):
            session.clear()
            session['usuario_id'] = usuario['id']
            session['email'] = usuario['email']
            session['usuario'] = usuario['nome']
            session['perfil'] = usuario['perfil']
            registrar_log(cursor, 'LOGIN', 'sistema', usuario['id'], 'Login realizado com sucesso')
            conn.commit()
            conn.close()
            return redirect(url_for('index'))

        # Tentativa falha: não existe usuário logado, então registramos o e-mail informado
        registrar_log(cursor, 'LOGIN_FALHA', 'sistema', usuario['id'] if usuario else None,
                      f"Tentativa de login com e-mail '{resumo(email, 100)}'",
                      usuario_id=0, usuario_nome='anonimo')
        conn.commit()
        conn.close()
        flash('E-mail ou senha incorretos.')
        return redirect(url_for('login'))

    return render_template('login.html')


@app.route('/logout')
def logout():
    if 'usuario_id' in session:
        conn = get_db()
        cursor = conn.cursor()
        registrar_log(cursor, 'LOGOUT', 'sistema', session['usuario_id'], 'Logout realizado')
        conn.commit()
        conn.close()
    session.clear()
    flash('Você saiu da sua conta.')
    return redirect(url_for('login'))


# ---------------------------------------------------------------- LISTAGEM

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
    pagina = max(request.args.get('pagina', 1, type=int) or 1, 1)
    itens_por_pagina = 10

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
    total_demandas = cursor.execute(
        f"SELECT COUNT(*) FROM demandas WHERE {condicao}", parametros
    ).fetchone()[0]
    total_paginas = max((total_demandas + itens_por_pagina - 1) // itens_por_pagina, 1)
    pagina = min(pagina, total_paginas)
    query = (
        f"SELECT *, "
        f"CASE WHEN status = 'Aberto' AND prazo < date('now', 'localtime') THEN 'Atrasado' ELSE status END AS status_exibido "
        f"FROM demandas WHERE {condicao} "
        f"ORDER BY {ORDENACOES[ordenar]} LIMIT ? OFFSET ?"
    )
    parametros_paginacao = parametros + [itens_por_pagina, (pagina - 1) * itens_por_pagina]
    demandas = cursor.execute(query, parametros_paginacao).fetchall()
    conn.close()

    return render_template(
        'index.html', demandas=demandas,
        ordenar=ordenar, tipo_busca=tipo_busca, termo=termo,
        pagina=pagina, total_paginas=total_paginas, total_demandas=total_demandas
    )


# ---------------------------------------------------------------- DEMANDAS

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
            (titulo, descricao, solicitante, solicitante_id, agora(), prioridade, prazo)
        )
        registrar_log(
            cursor, 'CRIAR_DEMANDA', 'demanda', cursor.lastrowid,
            f"titulo='{resumo(titulo)}'; prioridade={prioridade}; prazo={prazo}; descricao='{resumo(descricao)}'"
        )
        conn.commit()
        conn.close()

        flash('Salvo!')
        return redirect('/')

    return render_template('nova_demanda.html')


@app.route('/editar/<int:id>', methods=['GET', 'POST'])
def editar(id):
    conn = get_db()
    cursor = conn.cursor()
    demanda = cursor.execute('SELECT * FROM demandas WHERE id=?', (id,)).fetchone()

    if demanda is None:
        conn.close()
        flash('Demanda não encontrada.')
        return redirect('/')

    if request.method == 'POST':
        titulo = request.form['titulo']
        descricao = request.form['descricao']
        prioridade = request.form['prioridade']
        prazo = request.form['prazo']

        if caracteres_invalidos(titulo, prioridade):
            flash('Os campos (exceto descrição) não podem conter caracteres especiais.')
            conn.close()
            return redirect(f'/editar/{id}')

        # Compara antes x depois e registra só o que realmente mudou
        novos = {'titulo': titulo, 'descricao': descricao,
                 'prioridade': prioridade, 'prazo': prazo}
        mudancas = [
            f"{campo}: '{resumo(demanda[campo])}' -> '{resumo(valor)}'"
            for campo, valor in novos.items() if demanda[campo] != valor
        ]

        cursor.execute(
            "UPDATE demandas SET titulo=?, descricao=?, prioridade=?, prazo=? WHERE id=?",
            (titulo, descricao, prioridade, prazo, id)
        )
        if mudancas:
            registrar_log(cursor, 'EDITAR_DEMANDA', 'demanda', id, '; '.join(mudancas))
        conn.commit()
        conn.close()
        flash('Demanda atualizada.')
        return redirect('/')

    conn.close()
    return render_template('editar.html', demanda=demanda)


@app.route('/deletar/<int:id>', methods=['POST'])
def deletar(id):
    conn = get_db()
    cursor = conn.cursor()
    demanda = cursor.execute('SELECT * FROM demandas WHERE id=?', (id,)).fetchone()

    if demanda is None:
        conn.close()
        flash('Demanda não encontrada.')
        return redirect('/')

    # O log guarda um "retrato" da demanda, pois depois ela deixa de existir
    comentarios_removidos = cursor.execute(
        'DELETE FROM comentarios WHERE demanda_id=?', (id,)
    ).rowcount
    cursor.execute('DELETE FROM demandas WHERE id=?', (id,))
    registrar_log(
        cursor, 'EXCLUIR_DEMANDA', 'demanda', id,
        f"titulo='{resumo(demanda['titulo'])}'; solicitante={demanda['solicitante']} "
        f"(#{demanda['solicitante_id']}); prioridade={demanda['prioridade']}; "
        f"prazo={demanda['prazo']}; status={demanda['status']}; "
        f"comentarios_removidos={comentarios_removidos}"
    )
    conn.commit()
    conn.close()
    flash('Deletado!')
    return redirect('/')


@app.route('/detalhes/<int:id>')
def detalhes(id):
    conn = get_db()
    cursor = conn.cursor()
    demanda = cursor.execute(
        "SELECT *, "
        "CASE WHEN status = 'Aberto' AND prazo < date('now', 'localtime') THEN 'Atrasado' ELSE status END AS status_exibido "
        "FROM demandas WHERE id=?", (id,)
    ).fetchone()

    if demanda is None:
        conn.close()
        flash('Demanda não encontrada.')
        return redirect('/')

    comentarios = cursor.execute(
        'SELECT * FROM comentarios WHERE demanda_id=? ORDER BY id', (id,)
    ).fetchall()
    conn.close()

    return render_template('detalhes.html', demanda=demanda, comentarios=comentarios)


@app.route('/cancelar/<int:id>', methods=['POST'])
def cancelar(id):
    conn = get_db()
    cursor = conn.cursor()
    demanda = cursor.execute('SELECT status FROM demandas WHERE id=?', (id,)).fetchone()

    if demanda is None:
        conn.close()
        flash('Demanda não encontrada.')
        return redirect('/')

    if demanda['status'] in STATUS_FINAIS:
        conn.close()
        flash('Essa demanda já está finalizada e não pode ser cancelada.')
        return redirect(f'/detalhes/{id}')

    cursor.execute("UPDATE demandas SET status = 'Cancelado' WHERE id = ?", (id,))
    registrar_log(cursor, 'CANCELAR_DEMANDA', 'demanda', id,
                  f"status: '{demanda['status']}' -> 'Cancelado'")
    conn.commit()
    conn.close()
    flash('Demanda cancelada.')
    return redirect(f'/detalhes/{id}')


@app.route('/responsavel/<int:id>', methods=['POST'])
def assumir_responsabilidade(id):
    conn = get_db()
    cursor = conn.cursor()
    demanda = cursor.execute('SELECT status, responsavel_id FROM demandas WHERE id=?', (id,)).fetchone()

    if demanda is None:
        conn.close()
        flash('Demanda não encontrada.')
        return redirect('/')

    if demanda['status'] in STATUS_FINAIS:
        conn.close()
        flash('Essa demanda já está finalizada.')
        return redirect(f'/detalhes/{id}')

    if demanda['responsavel_id'] is not None:
        conn.close()
        flash('Essa demanda já tem um responsável.')
        return redirect(f'/detalhes/{id}')

    cursor.execute(
        'UPDATE demandas SET responsavel = ?, responsavel_id = ? WHERE id = ?',
        (session['usuario'], session['usuario_id'], id)
    )
    registrar_log(cursor, 'ASSUMIR_RESPONSABILIDADE', 'demanda', id,
                  f"responsavel: 'nenhum' -> '{session['usuario']} (#{session['usuario_id']})'")
    conn.commit()
    conn.close()
    flash('Você ficou responsável por essa demanda.')
    return redirect(f'/detalhes/{id}')


@app.route('/concluir/<int:id>', methods=['POST'])
def concluir(id):
    conn = get_db()
    cursor = conn.cursor()
    demanda = cursor.execute('SELECT status, responsavel_id FROM demandas WHERE id=?', (id,)).fetchone()

    if demanda is None:
        conn.close()
        flash('Demanda não encontrada.')
        return redirect('/')

    if demanda['status'] in STATUS_FINAIS:
        conn.close()
        flash('Essa demanda já está finalizada.')
        return redirect(f'/detalhes/{id}')

    if demanda['responsavel_id'] != session.get('usuario_id'):
        conn.close()
        flash('Só o responsável pela demanda pode concluí-la.')
        return redirect(f'/detalhes/{id}')

    cursor.execute(
        "UPDATE demandas SET status = 'Concluído', data_conclusao = ? WHERE id = ?",
        (agora(), id)
    )
    registrar_log(cursor, 'CONCLUIR_DEMANDA', 'demanda', id,
                  f"status: '{demanda['status']}' -> 'Concluído'")
    conn.commit()
    conn.close()
    flash('Demanda concluída!')
    return redirect(f'/detalhes/{id}')


# ---------------------------------------------------------------- COMENTÁRIOS

@app.route('/adicionar_comentario/<int:demanda_id>', methods=['POST'])
def adicionar_comentario(demanda_id):
    comentario = request.form.get('comentario', '').strip()
    autor = f"{session['usuario']} (#{session['usuario_id']})"

    if not comentario:
        flash('O comentário não pode estar vazio!')
        return redirect(f'/detalhes/{demanda_id}')

    if len(comentario) > 1000:
        flash('O comentário deve ter no máximo 1000 caracteres.')
        return redirect(f'/detalhes/{demanda_id}')

    conn = get_db()
    cursor = conn.cursor()

    existe = cursor.execute('SELECT id FROM demandas WHERE id=?', (demanda_id,)).fetchone()
    if existe is None:
        conn.close()
        flash('Demanda não encontrada.')
        return redirect('/')

    cursor.execute(
        "INSERT INTO comentarios (demanda_id, comentario, autor, data) VALUES (?, ?, ?, ?)",
        (demanda_id, comentario, autor, agora())
    )
    registrar_log(cursor, 'CRIAR_COMENTARIO', 'comentario', cursor.lastrowid,
                  f"demanda_id={demanda_id}; comentario='{comentario}'")
    conn.commit()
    conn.close()

    return redirect(f'/detalhes/{demanda_id}')


# ---------------------------------------------------------------- DASHBOARD

def _data_valida(texto):
    try:
        datetime.strptime(texto, '%Y-%m-%d')
        return texto
    except ValueError:
        return ''


def ler_filtros_dashboard(args):
    """Lê e valida os filtros da URL (valores inválidos são ignorados)."""
    responsavel = args.get('responsavel', '').strip()
    if responsavel != 'sem' and not responsavel.isdigit():
        responsavel = ''
    prioridade = args.get('prioridade', '')
    status = args.get('status', '')
    return {
        'data_inicio': _data_valida(args.get('data_inicio', '').strip()),
        'data_fim': _data_valida(args.get('data_fim', '').strip()),
        'responsavel': responsavel,
        'prioridade': prioridade if prioridade in PRIORIDADES else '',
        'status': status if status in STATUS_EXIBIDOS else '',
        'visao': 'criticas_atrasadas' if args.get('visao') == 'criticas_atrasadas' else '',
    }


def montar_condicao(f):
    """Monta o WHERE (sobre BASE_DEMANDAS) combinando todos os filtros."""
    condicoes, parametros = ['1=1'], []
    if f['data_inicio']:
        condicoes.append('date(data_criacao) >= ?')
        parametros.append(f['data_inicio'])
    if f['data_fim']:
        condicoes.append('date(data_criacao) <= ?')
        parametros.append(f['data_fim'])
    if f['responsavel'] == 'sem':
        condicoes.append('responsavel_id IS NULL')
    elif f['responsavel']:
        condicoes.append('responsavel_id = ?')
        parametros.append(int(f['responsavel']))
    if f['prioridade']:
        condicoes.append('prioridade = ?')
        parametros.append(f['prioridade'])
    if f['status']:
        condicoes.append('status_exibido = ?')
        parametros.append(f['status'])
    if f['visao'] == 'criticas_atrasadas':
        condicoes.append("prioridade = ? AND status_exibido = 'Atrasado'")
        parametros.append(PRIORIDADE_CRITICA)
    return ' AND '.join(condicoes), parametros


def _data_br(texto):
    return datetime.strptime(texto, '%Y-%m-%d').strftime('%d/%m/%Y')


def descrever_filtros(conn, f):
    """Texto dos filtros aplicados (usado no rodapé dos relatórios)."""
    partes = []
    if f['data_inicio'] and f['data_fim']:
        partes.append(f"Período {_data_br(f['data_inicio'])} a {_data_br(f['data_fim'])}")
    elif f['data_inicio']:
        partes.append(f"A partir de {_data_br(f['data_inicio'])}")
    elif f['data_fim']:
        partes.append(f"Até {_data_br(f['data_fim'])}")
    if f['responsavel'] == 'sem':
        partes.append('Sem responsável')
    elif f['responsavel']:
        linha = conn.execute('SELECT nome FROM solicitantes WHERE id=?',
                             (int(f['responsavel']),)).fetchone()
        partes.append(f"Responsável {linha['nome'] if linha else '#' + f['responsavel']}")
    if f['prioridade']:
        partes.append(f"Prioridade {f['prioridade']}")
    if f['status']:
        partes.append(f"Status {f['status']}")
    if f['visao'] == 'criticas_atrasadas':
        partes.append('Visão: críticas atrasadas')
    return ' · '.join(partes) or 'Nenhum filtro (todas as demandas)'


def calcular_dashboard(conn, f):
    cond, params = montar_condicao(f)
    base = BASE_DEMANDAS

    # Totais por status (Aberto = no prazo; Atrasado = aberto com prazo vencido)
    por_status = {s: 0 for s in STATUS_EXIBIDOS}
    for linha in conn.execute(
            f"SELECT status_exibido AS s, COUNT(*) AS n FROM {base} WHERE {cond} GROUP BY s", params):
        por_status[linha['s']] = linha['n']
    total = sum(por_status.values())

    def pct(n):
        return round(n * 100 / total, 1) if total else 0

    # Por prioridade
    por_prioridade = {p: 0 for p in PRIORIDADES}
    for linha in conn.execute(
            f"SELECT prioridade AS p, COUNT(*) AS n FROM {base} WHERE {cond} GROUP BY p", params):
        por_prioridade[linha['p']] = linha['n']

    # Críticas: quantas, quem são e status atual (atrasadas primeiro)
    criticas = [dict(r) for r in conn.execute(
        f"SELECT id, titulo, solicitante, responsavel, prazo, status_exibido "
        f"FROM {base} WHERE {cond} AND prioridade = ? "
        f"ORDER BY (status_exibido = 'Atrasado') DESC, prazo ASC",
        params + [PRIORIDADE_CRITICA])]
    criticas_atrasadas = [c for c in criticas if c['status_exibido'] == 'Atrasado']

    # Demandas em aberto (no prazo + atrasadas) por responsável
    por_responsavel = [dict(r) for r in conn.execute(
        f"SELECT COALESCE(responsavel, 'Sem responsável') AS nome, "
        f"       (responsavel_id IS NULL) AS sem_responsavel, "
        f"       COUNT(*) AS abertas, "
        f"       SUM(CASE WHEN status_exibido = 'Atrasado' THEN 1 ELSE 0 END) AS atrasadas "
        f"FROM {base} WHERE {cond} AND status_exibido IN ('Aberto', 'Atrasado') "
        f"GROUP BY responsavel_id, nome ORDER BY abertas DESC, nome", params)]
    sem_responsavel = sum(r['abertas'] for r in por_responsavel if r['sem_responsavel'])

    # Tempo médio de resolução (dias): só concluídas COM responsável; canceladas
    # e demandas sem responsável ficam fora (fora do SLA); críticas têm peso maior.
    concluidas = conn.execute(
        f"SELECT prioridade, julianday(data_conclusao) - julianday(data_criacao) AS dias "
        f"FROM {base} WHERE {cond} AND status = 'Concluído' "
        f"AND responsavel_id IS NOT NULL AND data_conclusao IS NOT NULL", params).fetchall()
    soma_pesos = soma_ponderada = soma_simples = 0
    for linha in concluidas:
        dias = max(linha['dias'] or 0, 0)
        peso = PESO_CRITICA if linha['prioridade'] == PRIORIDADE_CRITICA else 1
        soma_pesos += peso
        soma_ponderada += dias * peso
        soma_simples += dias
    fora_sla = conn.execute(
        f"SELECT COUNT(*) FROM {base} WHERE {cond} AND status = 'Concluído' "
        f"AND responsavel_id IS NULL", params).fetchone()[0]
    tempo_medio = {
        'dias': round(soma_ponderada / soma_pesos, 1) if soma_pesos else None,
        'dias_simples': round(soma_simples / len(concluidas), 1) if concluidas else None,
        'consideradas': len(concluidas),
        'fora_sla': fora_sla,
        'peso_critica': PESO_CRITICA,
    }

    # Evolução temporal: criadas x concluídas (por dia se o período for curto, senão por mês)
    formato = '%Y-%m'
    if f['data_inicio'] and f['data_fim']:
        dias_periodo = (datetime.strptime(f['data_fim'], '%Y-%m-%d')
                        - datetime.strptime(f['data_inicio'], '%Y-%m-%d')).days
        if dias_periodo <= 62:
            formato = '%Y-%m-%d'
    criadas = {r['p']: r['n'] for r in conn.execute(
        f"SELECT strftime('{formato}', data_criacao) AS p, COUNT(*) AS n "
        f"FROM {base} WHERE {cond} GROUP BY p", params)}
    fechadas = {r['p']: r['n'] for r in conn.execute(
        f"SELECT strftime('{formato}', data_conclusao) AS p, COUNT(*) AS n "
        f"FROM {base} WHERE {cond} AND status = 'Concluído' AND data_conclusao IS NOT NULL "
        f"GROUP BY p", params)}
    periodos = sorted(set(criadas) | set(fechadas))
    evolucao = {
        'labels': periodos,
        'criadas': [criadas.get(p, 0) for p in periodos],
        'concluidas': [fechadas.get(p, 0) for p in periodos],
    }

    return {
        'total': total,
        'status': {s: {'n': n, 'pct': pct(n)} for s, n in por_status.items()},
        'prioridade': por_prioridade,
        'criticas': criticas,
        'criticas_atrasadas': criticas_atrasadas,
        'por_responsavel': por_responsavel,
        'sem_responsavel': sem_responsavel,
        'tempo_medio': tempo_medio,
        'evolucao': evolucao,
        'filtros_texto': descrever_filtros(conn, f),
    }


def linhas_relatorio(conn, f):
    cond, params = montar_condicao(f)
    return conn.execute(
        f"SELECT id, titulo, solicitante, prioridade, prazo, status_exibido, "
        f"       COALESCE(responsavel, 'Sem responsável') AS responsavel, "
        f"       data_criacao, data_conclusao "
        f"FROM {BASE_DEMANDAS} WHERE {cond} ORDER BY {ORDENACOES['prioridade']}", params
    ).fetchall()


def _seguro_csv(valor):
    """Evita que o Excel execute fórmulas vindas de texto digitado por usuários."""
    texto = '' if valor is None else str(valor)
    return "'" + texto if texto[:1] in ('=', '+', '-', '@') else texto


@app.route('/dashboard')
def dashboard():
    filtros = ler_filtros_dashboard(request.args)
    conn = get_db()
    responsaveis = conn.execute('SELECT id, nome FROM solicitantes ORDER BY nome').fetchall()
    conn.close()
    return render_template('dashboard.html', filtros=filtros, responsaveis=responsaveis,
                           prioridades=PRIORIDADES, status_lista=STATUS_EXIBIDOS)


@app.route('/dashboard/dados')
def dashboard_dados():
    """JSON usado pelo dashboard para atualizar KPIs e gráficos sem recarregar a página."""
    filtros = ler_filtros_dashboard(request.args)
    conn = get_db()
    dados = calcular_dashboard(conn, filtros)
    conn.close()
    return jsonify(dados)


@app.route('/dashboard/exportar')
def dashboard_exportar():
    """Exporta CSV (abre no Excel) respeitando todos os filtros e a visão ativa."""
    filtros = ler_filtros_dashboard(request.args)
    conn = get_db()
    cursor = conn.cursor()
    linhas = linhas_relatorio(conn, filtros)
    descricao = descrever_filtros(conn, filtros)

    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=';')
    escritor.writerow([NOME_EMPRESA])
    escritor.writerow(['Relatório de demandas'])
    escritor.writerow([f"Gerado em {datetime.now().strftime('%d/%m/%Y às %H:%M')}"])
    escritor.writerow([])
    escritor.writerow(['ID', 'Título', 'Solicitante', 'Prioridade', 'Prazo', 'Status',
                       'Responsável', 'Criada em', 'Concluída em'])
    for l in linhas:
        escritor.writerow([l['id'], _seguro_csv(l['titulo']), _seguro_csv(l['solicitante']),
                           l['prioridade'], l['prazo'], l['status_exibido'],
                           _seguro_csv(l['responsavel']), l['data_criacao'],
                           l['data_conclusao'] or ''])
    escritor.writerow([])
    escritor.writerow([f'Total: {len(linhas)} demanda(s)'])
    escritor.writerow([f'Relatório gerado com: {descricao}'])

    registrar_log(cursor, 'EXPORTAR_RELATORIO', 'relatorio', None,
                  f"formato=csv; linhas={len(linhas)}; filtros={descricao}")
    conn.commit()
    conn.close()

    nome_arquivo = f"relatorio_demandas_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    # BOM (\ufeff) faz o Excel reconhecer os acentos corretamente
    return Response('\ufeff' + saida.getvalue(), mimetype='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename={nome_arquivo}'})


@app.route('/dashboard/relatorio')
def dashboard_relatorio():
    """Versão para impressão / 'Salvar como PDF' do navegador, com cabeçalho e filtros no rodapé."""
    filtros = ler_filtros_dashboard(request.args)
    conn = get_db()
    cursor = conn.cursor()
    dados = calcular_dashboard(conn, filtros)
    linhas = linhas_relatorio(conn, filtros)
    registrar_log(cursor, 'EXPORTAR_RELATORIO', 'relatorio', None,
                  f"formato=impressao/pdf; linhas={len(linhas)}; filtros={dados['filtros_texto']}")
    conn.commit()
    conn.close()
    return render_template('relatorio.html', empresa=NOME_EMPRESA, dados=dados, linhas=linhas,
                           gerado_em=datetime.now().strftime('%d/%m/%Y às %H:%M'))


# ---------------------------------------------------------------- AUDITORIA

@app.route('/logs')
def ver_logs():
    acao = request.args.get('acao', '')
    if acao not in ACOES_LOG:
        acao = ''
    pagina = max(request.args.get('pagina', 1, type=int) or 1, 1)
    por_pagina = 20

    condicao, parametros = ('1=1', []) if not acao else ('acao = ?', [acao])

    conn = get_db()
    total = conn.execute(f"SELECT COUNT(*) FROM logs WHERE {condicao}", parametros).fetchone()[0]
    total_paginas = max((total + por_pagina - 1) // por_pagina, 1)
    pagina = min(pagina, total_paginas)
    logs = conn.execute(
        f"SELECT * FROM logs WHERE {condicao} ORDER BY id DESC LIMIT ? OFFSET ?",
        parametros + [por_pagina, (pagina - 1) * por_pagina]
    ).fetchall()
    conn.close()

    return render_template('logs.html', logs=logs, acao=acao, acoes=ACOES_LOG,
                           pagina=pagina, total_paginas=total_paginas, total=total)


if __name__ == '__main__':
    # debug só quando FLASK_DEBUG=1; host aberto só se SGDI_HOST for definido
    app.run(debug=os.environ.get('FLASK_DEBUG') == '1',
            host=os.environ.get('SGDI_HOST', '127.0.0.1'))