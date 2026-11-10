import csv
import io
import os
from datetime import datetime
from functools import wraps

from flask import (Flask, render_template, request, redirect, url_for, flash,
                   session, jsonify, Response)
from werkzeug.security import check_password_hash

import servicos
from api import api_bp
from core import (ACOES_LOG, NOME_EMPRESA, ORDENACOES, PRIORIDADES, ROTULOS_PERFIL,
                  STATUS_EXIBIDOS, calcular_dashboard, caminho_banco, caracteres_invalidos,
                  descrever_filtros, garantir_tabela_tokens, get_db, ler_filtros_dashboard,
                  linhas_relatorio, perfil_atual, pode, registrar_log, requisicao_api,
                  resumo, usuario_atual)
from servicos import ErroRegra, NAO_ENCONTRADO

# Constantes, perfis/permissões, get_db, registrar_log, pode() etc. ficam em core.py,
# compartilhados com a API (api.py). Nunca importe o app.py de outro módulo: ele roda
# como __main__ e a importação criaria uma segunda aplicação.

app = Flask(__name__)
# Em produção defina a variável de ambiente SECRET_KEY
app.secret_key = os.environ.get('SECRET_KEY', 'dev-only-troque-em-producao')
app.json.ensure_ascii = False                    # JSON com acentos legíveis (API e dashboard)
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024   # corpo de requisição até 1 MB (413 acima disso)

# Banco criado antes da API ganha a tabela de tokens sem precisar rodar o init_db.py
if os.path.exists(caminho_banco()):
    _conn = get_db()
    garantir_tabela_tokens(_conn)
    _conn.close()


def requer_permissao(permissao, resposta_json=False):
    """Bloqueia a rota (no servidor) para quem não tem a permissão e registra no log."""
    def decorador(funcao):
        @wraps(funcao)
        def interna(*args, **kwargs):
            if pode(permissao):
                return funcao(*args, **kwargs)

            conn = get_db()
            cursor = conn.cursor()
            registrar_log(cursor, 'ACESSO_NEGADO', 'sistema', None,
                          f"permissao={permissao}; rota={request.path}; perfil={perfil_atual()}")
            conn.commit()
            conn.close()

            if resposta_json:
                return jsonify(erro='Acesso negado'), 403
            flash('Você não tem permissão para acessar essa área.')
            # Evita loop: quem nem pode visualizar a listagem é desconectado
            if request.endpoint != 'index' and pode('visualizar'):
                return redirect(url_for('index'))
            return redirect(url_for('logout'))
        return interna
    return decorador


@app.context_processor
def injetar_perfil():
    """Deixa pode('permissao') e o rótulo do perfil disponíveis em todos os templates."""
    return {'pode': pode, 'perfil_rotulo': ROTULOS_PERFIL[perfil_atual()]}


@app.before_request
def verificar_login():
    # A API (/api/...) tem autenticação própria por token e responde 401 em JSON;
    # redirecionar para a tela de login (HTML) não faz sentido para ela.
    if requisicao_api():
        return None
    if 'email' not in session and request.endpoint not in ['login', 'logout', 'static']:
        return redirect(url_for('login'))


app.register_blueprint(api_bp)   # API REST em /api/v1


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
            registrar_log(cursor, 'LOGIN', 'sistema', usuario['id'],
                          f"Login realizado com sucesso (perfil={usuario['perfil']})")
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

@app.route('/')
@requer_permissao('visualizar')
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
# As regras de negócio (validação, permissões de status, logs) ficam em servicos.py,
# compartilhadas com a API. Aqui só cuidamos da tela: ler o formulário, chamar o
# serviço, mostrar o flash e redirecionar.

CAMPOS_FORM = ('titulo', 'descricao', 'prioridade', 'prazo')


def _acao_na_tela(servico, id, sucesso, destino, *args):
    """Executa um serviço com commit; em caso de erro mostra o aviso e volta para a tela certa."""
    conn = get_db()
    try:
        servico(conn.cursor(), id, *args)
        conn.commit()
    except ErroRegra as erro:
        flash(erro.texto_para_tela())
        return redirect('/' if erro.tipo == NAO_ENCONTRADO else destino)
    finally:
        conn.close()
    if sucesso:
        flash(sucesso)
    return redirect(destino)


@app.route('/nova_demanda', methods=['GET', 'POST'])
@requer_permissao('criar_demanda')
def nova_demanda():
    if request.method == 'POST':
        dados = {campo: request.form.get(campo, '') for campo in CAMPOS_FORM}
        conn = get_db()
        try:
            servicos.criar_demanda(conn.cursor(), usuario_atual(), dados)
            conn.commit()
        except ErroRegra as erro:
            flash(erro.texto_para_tela())
            return redirect('/nova_demanda')
        finally:
            conn.close()

        flash('Salvo!')
        return redirect('/')

    return render_template('nova_demanda.html')


@app.route('/editar/<int:id>', methods=['GET', 'POST'])
@requer_permissao('editar_demanda')
def editar(id):
    if request.method == 'POST':
        dados = {campo: request.form.get(campo, '') for campo in CAMPOS_FORM}
        conn = get_db()
        try:
            servicos.editar_demanda(conn.cursor(), id, dados)
            conn.commit()
        except ErroRegra as erro:
            flash(erro.texto_para_tela())
            return redirect('/' if erro.tipo == NAO_ENCONTRADO else f'/editar/{id}')
        finally:
            conn.close()
        flash('Demanda atualizada.')
        return redirect('/')

    conn = get_db()
    demanda = conn.execute('SELECT * FROM demandas WHERE id=?', (id,)).fetchone()
    conn.close()
    if demanda is None:
        flash('Demanda não encontrada.')
        return redirect('/')
    return render_template('editar.html', demanda=demanda)


@app.route('/deletar/<int:id>', methods=['POST'])
@requer_permissao('excluir_demanda')
def deletar(id):
    return _acao_na_tela(servicos.excluir_demanda, id, 'Deletado!', '/')


@app.route('/detalhes/<int:id>')
@requer_permissao('visualizar')
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
@requer_permissao('alterar_status')
def cancelar(id):
    return _acao_na_tela(servicos.cancelar_demanda, id, 'Demanda cancelada.',
                         f'/detalhes/{id}', usuario_atual())


@app.route('/responsavel/<int:id>', methods=['POST'])
@requer_permissao('alterar_status')
def assumir_responsabilidade(id):
    return _acao_na_tela(servicos.assumir_responsabilidade, id,
                         'Você ficou responsável por essa demanda.',
                         f'/detalhes/{id}', usuario_atual())


@app.route('/concluir/<int:id>', methods=['POST'])
@requer_permissao('alterar_status')
def concluir(id):
    return _acao_na_tela(servicos.concluir_demanda, id, 'Demanda concluída!',
                         f'/detalhes/{id}', usuario_atual())


# ---------------------------------------------------------------- COMENTÁRIOS

@app.route('/adicionar_comentario/<int:demanda_id>', methods=['POST'])
@requer_permissao('comentar')
def adicionar_comentario(demanda_id):
    return _acao_na_tela(servicos.adicionar_comentario, demanda_id, None,
                         f'/detalhes/{demanda_id}', usuario_atual(),
                         request.form.get('comentario', ''))


# ---------------------------------------------------------------- DASHBOARD

def _seguro_csv(valor):
    """Evita que o Excel execute fórmulas vindas de texto digitado por usuários."""
    texto = '' if valor is None else str(valor)
    return "'" + texto if texto[:1] in ('=', '+', '-', '@') else texto


@app.route('/dashboard')
@requer_permissao('ver_painel')
def dashboard():
    filtros = ler_filtros_dashboard(request.args)
    conn = get_db()
    responsaveis = conn.execute('SELECT id, nome FROM solicitantes ORDER BY nome').fetchall()
    conn.close()
    return render_template('dashboard.html', filtros=filtros, responsaveis=responsaveis,
                           prioridades=PRIORIDADES, status_lista=STATUS_EXIBIDOS)


@app.route('/dashboard/dados')
@requer_permissao('ver_painel', resposta_json=True)
def dashboard_dados():
    """JSON usado pelo dashboard para atualizar KPIs e gráficos sem recarregar a página."""
    filtros = ler_filtros_dashboard(request.args)
    conn = get_db()
    dados = calcular_dashboard(conn, filtros)
    conn.close()
    return jsonify(dados)


@app.route('/dashboard/exportar')
@requer_permissao('ver_painel')
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
@requer_permissao('ver_painel')
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
@requer_permissao('ver_logs')
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