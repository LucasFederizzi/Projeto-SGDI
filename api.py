"""
API REST do SGDI (versão 1) - prefixo /api/v1.

Fases implementadas aqui: 1 (fundação), 2 (autenticação), 3 (leitura) e 4 (escrita).
  Autenticação:  POST/DELETE /auth/token, GET /me
  Leitura:       GET /demandas, /demandas/{id}, /demandas/{id}/comentarios, /dashboard, /logs
  Escrita:       POST /demandas, PATCH/DELETE /demandas/{id},
                 POST /demandas/{id}/responsavel|concluir|cancelar, POST /demandas/{id}/comentarios

As regras de negócio NÃO ficam aqui: moram em servicos.py e são as mesmas das telas.

Padrões:
  - sucesso:  {"dados": ...}
  - erro:     {"erro": {"codigo": "...", "mensagem": "...", "campos": {...}}}
  - autenticação SOMENTE por "Authorization: Bearer <token>" (cookie de sessão é ignorado).

Este módulo importa apenas de core.py (nunca de app.py; ver explicação no core.py).
"""
import re
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta
from functools import wraps
from math import ceil

from flask import Blueprint, Response, g, jsonify, request, url_for
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

import servicos
from core import (ACOES_LOG, API_TOKEN_HORAS, BASE_DEMANDAS, ORDENACOES, PERMISSOES_POR_PERFIL,
                  PRIORIDADES, ROTULOS_PERFIL, STATUS_EXIBIDOS, TITULO_SEM_PONTUACAO_API,
                  agora, calcular_dashboard, get_db, hash_token, ler_filtros_dashboard,
                  perfil_atual, pode, registrar_log, requisicao_api, resumo)
from servicos import (CONFLITO, NAO_ENCONTRADO, PROIBIDO, REQUISICAO_INVALIDA, VALIDACAO,
                      ErroRegra)

api_bp = Blueprint('api', __name__, url_prefix='/api/v1')

FORMATO_DATA_HORA = '%Y-%m-%d %H:%M:%S'

# Proteção contra força bruta no login da API (por IP, contando falhas no log)
LOGIN_MAX_FALHAS = 5
LOGIN_JANELA_MINUTOS = 15

# Hash de mentira: usado quando o e-mail não existe, para o tempo de resposta
# ser parecido com o de uma senha errada (não revela quais e-mails existem).
_HASH_FALSO = generate_password_hash('senha-que-nunca-sera-usada')

_DESAFIO_BEARER = {'WWW-Authenticate': 'Bearer realm="SGDI API"'}


# ---------------------------------------------------------------- RESPOSTAS PADRONIZADAS

def ok(dados, status=200, **extras):
    """Resposta de sucesso: {"dados": ...} (listas podem levar extras, ex.: paginacao)."""
    resposta = jsonify(dados=dados, **extras)
    resposta.status_code = status
    return resposta


def sem_conteudo():
    return Response(status=204)


def erro(status, codigo, mensagem, campos=None, headers=None):
    """Resposta de erro no formato fixo da API."""
    resposta = jsonify(erro={'codigo': codigo, 'mensagem': mensagem, 'campos': campos or {}})
    resposta.status_code = status
    for nome, valor in (headers or {}).items():
        resposta.headers[nome] = valor
    return resposta


def serializar_usuario(usuario):
    """Lista EXPLÍCITA de campos: nunca use dict(row) em solicitantes (tem o hash da senha)."""
    perfil = usuario['perfil']
    return {
        'id': usuario['id'],
        'nome': usuario['nome'],
        'email': usuario['email'],
        'perfil': perfil,
        'perfil_rotulo': ROTULOS_PERFIL.get(perfil, perfil),
    }


# ---------------------------------------------------------------- HOOKS DO BLUEPRINT

@api_bp.before_request
def marcar_origem_api():
    # registrar_log (core.py) acrescenta "origem=api" aos detalhes quando isto está setado.
    g.origem = 'api'


@api_bp.after_app_request
def cabecalhos_de_seguranca(resposta):
    # Vale para tudo que está em /api, inclusive os erros que o Flask gera antes de
    # chegar numa rota (404/405/413...). Respostas podem ter dados sensíveis (ex.: o
    # token): nada de cache; e o navegador não deve "adivinhar" o tipo do conteúdo.
    if requisicao_api():
        resposta.headers['Cache-Control'] = 'no-store'
        resposta.headers['X-Content-Type-Options'] = 'nosniff'
    return resposta


# ---------------------------------------------------------------- ERROS EM JSON

# O Flask devolve HTML em 404/405/500. Para /api/ queremos JSON; para o resto do
# sistema o comportamento continua o mesmo (devolvemos a própria exceção).
_ERROS_HTTP = {
    400: ('requisicao_invalida', 'Requisição inválida.'),
    404: ('nao_encontrado', 'Recurso não encontrado.'),
    405: ('metodo_nao_permitido', 'Método não permitido para este recurso.'),
    413: ('corpo_grande_demais', 'O corpo da requisição é grande demais.'),
    415: ('tipo_nao_suportado', 'Tipo de conteúdo não suportado.'),
    500: ('erro_interno', 'Erro interno do servidor.'),
}


@api_bp.app_errorhandler(HTTPException)
def erros_http_em_json(e):
    if not requisicao_api():
        return e
    codigo, mensagem = _ERROS_HTTP.get(e.code, ('erro_http', e.name))
    headers = {}
    metodos = getattr(e, 'valid_methods', None)
    if e.code == 405 and metodos:
        headers['Allow'] = ', '.join(metodos)
    return erro(e.code, codigo, mensagem, headers=headers)


# ---------------------------------------------------------------- AUTENTICAÇÃO POR TOKEN

def _token_do_cabecalho():
    partes = request.headers.get('Authorization', '').split(None, 1)
    if len(partes) == 2 and partes[0].lower() == 'bearer' and partes[1].strip():
        return partes[1].strip()
    return None


def requer_token_api(permissao=None):
    """
    Protege uma rota da API.
      - sem token / token inválido, revogado ou expirado -> 401 (JSON)
      - token válido mas perfil sem a permissão          -> 403 (JSON) + log ACESSO_NEGADO
    O usuário e o PERFIL são lidos do banco a cada requisição: se o perfil mudar,
    vale na hora. Depois do decorator, g.usuario_api e g.token_id estão preenchidos.
    """
    def decorador(funcao):
        @wraps(funcao)
        def interna(*args, **kwargs):
            token = _token_do_cabecalho()
            if token is None:
                return erro(401, 'nao_autenticado',
                            'Envie o cabeçalho "Authorization: Bearer <token>".',
                            headers=_DESAFIO_BEARER)

            conn = get_db()
            try:
                linha = conn.execute(
                    """SELECT t.id AS token_id, t.expira_em, t.revogado_em,
                              s.id, s.nome, s.email, s.perfil
                       FROM api_tokens t JOIN solicitantes s ON s.id = t.usuario_id
                       WHERE t.token_hash = ?""",
                    (hash_token(token),)
                ).fetchone()

                if linha is None or linha['revogado_em'] is not None:
                    return erro(401, 'token_invalido', 'Token inválido ou revogado.',
                                headers=_DESAFIO_BEARER)
                if datetime.now() >= datetime.strptime(linha['expira_em'], FORMATO_DATA_HORA):
                    return erro(401, 'token_expirado',
                                'Token expirado. Gere outro em POST /api/v1/auth/token.',
                                headers=_DESAFIO_BEARER)

                g.usuario_api = {k: linha[k] for k in ('id', 'nome', 'email', 'perfil')}
                g.token_id = linha['token_id']
                g.token_expira_em = linha['expira_em']

                if permissao and not pode(permissao):
                    registrar_log(conn.cursor(), 'ACESSO_NEGADO', 'sistema', None,
                                  f"permissao={permissao}; rota={request.path}; perfil={perfil_atual()}")
                    conn.commit()
                    return erro(403, 'acesso_negado',
                                'Seu perfil não tem permissão para esta operação.')
            finally:
                conn.close()

            return funcao(*args, **kwargs)
        # Fica visível para os testes de segurança (test_api_seguranca.py): eles percorrem
        # TODAS as rotas e conferem se cada uma tem decorator e a permissão certa.
        interna.permissao_exigida = permissao
        return interna
    return decorador


# ---------------------------------------------------------------- LOGIN: FORÇA BRUTA

def _segundos_ate_liberar(cursor):
    """0 se o IP pode tentar login; senão, em quantos segundos a tentativa libera."""
    inicio_janela = datetime.now() - timedelta(minutes=LOGIN_JANELA_MINUTOS)
    falhas = cursor.execute(
        "SELECT data_hora FROM logs WHERE acao = 'API_LOGIN_FALHA' AND ip IS ? "
        "AND data_hora >= ? ORDER BY data_hora",
        (request.remote_addr, inicio_janela.strftime(FORMATO_DATA_HORA))
    ).fetchall()
    if len(falhas) < LOGIN_MAX_FALHAS:
        return 0
    # Libera quando a falha que deixa o total no limite sair da janela
    marco = datetime.strptime(falhas[len(falhas) - LOGIN_MAX_FALHAS]['data_hora'], FORMATO_DATA_HORA)
    restante = (marco + timedelta(minutes=LOGIN_JANELA_MINUTOS) - datetime.now()).total_seconds()
    return max(ceil(restante), 1)


# ---------------------------------------------------------------- ENDPOINTS

@api_bp.route('/auth/token', methods=['POST'])
def emitir_token():
    """Troca e-mail + senha por um token Bearer (o token só aparece nesta resposta)."""
    corpo = request.get_json(silent=True)
    if not isinstance(corpo, dict):
        return erro(400, 'requisicao_invalida',
                    'Envie um corpo JSON (Content-Type: application/json) com "email" e "senha".')

    email, senha = corpo.get('email'), corpo.get('senha')
    campos = {}
    if not isinstance(email, str) or not email.strip():
        campos['email'] = 'Campo obrigatório (texto).'
    if not isinstance(senha, str) or not senha:
        campos['senha'] = 'Campo obrigatório (texto).'
    if campos:
        return erro(422, 'validacao', 'Dados inválidos.', campos)
    email = email.strip().lower()

    conn = get_db()
    try:
        cursor = conn.cursor()

        espera = _segundos_ate_liberar(cursor)
        if espera:
            return erro(429, 'muitas_tentativas',
                        'Muitas tentativas de login falhas. Tente novamente mais tarde.',
                        headers={'Retry-After': str(espera)})

        usuario = cursor.execute(
            "SELECT id, nome, email, senha, perfil FROM solicitantes WHERE LOWER(email) = ?",
            (email,)
        ).fetchone()

        senha_confere = check_password_hash(usuario['senha'] if usuario else _HASH_FALSO, senha)
        if usuario is None or not senha_confere:
            registrar_log(cursor, 'API_LOGIN_FALHA', 'sistema', usuario['id'] if usuario else None,
                          f"Tentativa de login pela API com e-mail '{resumo(email, 100)}'",
                          usuario_id=0, usuario_nome='anonimo')
            conn.commit()
            return erro(401, 'credenciais_invalidas', 'E-mail ou senha incorretos.',
                        headers=_DESAFIO_BEARER)

        token = secrets.token_urlsafe(32)
        agora_dt = datetime.now()
        expira_em = (agora_dt + timedelta(hours=API_TOKEN_HORAS)).strftime(FORMATO_DATA_HORA)
        cursor.execute(
            "INSERT INTO api_tokens (token_hash, usuario_id, criado_em, expira_em, ip) "
            "VALUES (?, ?, ?, ?, ?)",
            (hash_token(token), usuario['id'], agora(), expira_em, request.remote_addr)
        )
        # Faxina: tokens vencidos há mais de 7 dias não servem mais nem para consulta
        cursor.execute("DELETE FROM api_tokens WHERE expira_em < ?",
                       ((agora_dt - timedelta(days=7)).strftime(FORMATO_DATA_HORA),))
        registrar_log(cursor, 'API_LOGIN', 'sistema', usuario['id'],
                      f"Token emitido (perfil={usuario['perfil']}; expira_em={expira_em})",
                      usuario_id=usuario['id'], usuario_nome=usuario['nome'])
        conn.commit()
    finally:
        conn.close()

    return ok({
        'token': token,
        'tipo': 'Bearer',
        'expira_em': expira_em,
        'expira_em_segundos': int(API_TOKEN_HORAS * 3600),
        'usuario': serializar_usuario(usuario),
    })


@api_bp.route('/auth/token', methods=['DELETE'])
@requer_token_api()
def revogar_token():
    """Revoga o token usado na requisição (logout da API)."""
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE api_tokens SET revogado_em = ? WHERE id = ?", (agora(), g.token_id))
        registrar_log(cursor, 'API_LOGOUT', 'sistema', g.usuario_api['id'], 'Token revogado')
        conn.commit()
    finally:
        conn.close()
    return sem_conteudo()


@api_bp.route('/me', methods=['GET'])
@requer_token_api()
def me():
    """Quem sou eu: usuário do token, permissões do perfil e validade do token."""
    return ok({
        'usuario': serializar_usuario(g.usuario_api),
        'permissoes': sorted(PERMISSOES_POR_PERFIL[perfil_atual()]),
        'token': {'expira_em': g.token_expira_em},
    })


# ================================================================ FASES 3 e 4: DEMANDAS

# Maior id que o SQLite aceita sem estourar (evita erro 500 com ids gigantes na URL)
ID = '<int(min=1, max=2147483647):id>'

_STATUS_POR_TIPO = {
    REQUISICAO_INVALIDA: 400, PROIBIDO: 403, NAO_ENCONTRADO: 404, CONFLITO: 409, VALIDACAO: 422,
}


@api_bp.errorhandler(ErroRegra)
def erro_de_regra(e):
    """Qualquer regra violada nos serviços vira o JSON de erro padrão da API."""
    return erro(_STATUS_POR_TIPO[e.tipo], e.codigo, e.mensagem, e.campos)


@contextmanager
def transacao():
    """Abre o banco, faz commit se o bloco terminar bem e descarta tudo se der erro."""
    conn = get_db()
    try:
        yield conn.cursor()
        conn.commit()
    finally:
        conn.close()


def _corpo_json():
    corpo = request.get_json(silent=True)
    if not isinstance(corpo, dict):
        raise ErroRegra(REQUISICAO_INVALIDA, 'requisicao_invalida',
                        'Envie um corpo JSON (Content-Type: application/json) com um objeto.')
    return corpo


# ---------------------------------------------------------------- SERIALIZAÇÃO (campos explícitos)

CAMPOS_DEMANDA = ('id', 'titulo', 'descricao', 'solicitante', 'solicitante_id', 'responsavel',
                  'responsavel_id', 'prioridade', 'prazo', 'status', 'status_exibido',
                  'data_criacao', 'data_conclusao')


def serializar_demanda(linha):
    """`status` é o gravado; `status_exibido` inclui "Atrasado" (aberta com prazo vencido)."""
    return {campo: linha[campo] for campo in CAMPOS_DEMANDA}


def serializar_comentario(linha):
    return {'id': linha['id'], 'demanda_id': linha['demanda_id'], 'autor': linha['autor'],
            'comentario': linha['comentario'], 'criado_em': linha['data']}


def serializar_log(linha):
    return {campo: linha[campo] for campo in ('id', 'data_hora', 'usuario_id', 'usuario_nome',
                                               'acao', 'entidade', 'entidade_id', 'detalhes', 'ip')}


def _obter_demanda(cursor, id):
    linha = cursor.execute(f"SELECT * FROM {BASE_DEMANDAS} WHERE id = ?", (id,)).fetchone()
    if linha is None:
        raise ErroRegra(NAO_ENCONTRADO, 'demanda_nao_encontrada', 'Demanda não encontrada.')
    return linha


# ---------------------------------------------------------------- LEITURA DE QUERY STRING

_DATA = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def _texto(nome):
    return request.args.get(nome, '').strip()


def _inteiro(nome, padrao, minimo, maximo, campos):
    bruto = _texto(nome)
    if not bruto:
        return padrao
    try:
        valor = int(bruto)
    except ValueError:
        campos[nome] = 'Deve ser um número inteiro.'
        return padrao
    if valor < minimo or valor > maximo:
        campos[nome] = f'Deve estar entre {minimo} e {maximo}.'
        return padrao
    return valor


def _data(nome, campos):
    bruto = _texto(nome)
    if not bruto:
        return ''
    try:
        if not _DATA.match(bruto):
            raise ValueError
        datetime.strptime(bruto, '%Y-%m-%d')
    except ValueError:
        campos[nome] = 'Use o formato AAAA-MM-DD com uma data válida.'
        return ''
    return bruto


def _ler_paginacao(campos):
    pagina = _inteiro('pagina', 1, 1, 1_000_000, campos)
    por_pagina = _inteiro('por_pagina', 20, 1, 100, campos)
    return pagina, por_pagina


def _paginacao(total, pagina, por_pagina):
    return {'pagina': pagina, 'por_pagina': por_pagina, 'total': total,
            'total_paginas': max(ceil(total / por_pagina), 1)}


def _escapar_like(texto):
    return texto.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def _filtros_demandas():
    """Lê e valida os filtros da listagem. Valor inválido = 422 (a API não ignora em silêncio)."""
    campos = {}
    condicoes, params = ['1=1'], []

    status = _texto('status')
    if status:
        if status in STATUS_EXIBIDOS:
            condicoes.append('status_exibido = ?')
            params.append(status)
        else:
            campos['status'] = 'Use um destes valores: ' + ', '.join(STATUS_EXIBIDOS) + '.'

    prioridade = _texto('prioridade')
    if prioridade:
        if prioridade in PRIORIDADES:
            condicoes.append('prioridade = ?')
            params.append(prioridade)
        else:
            campos['prioridade'] = 'Use um destes valores: ' + ', '.join(PRIORIDADES) + '.'

    responsavel = _texto('responsavel_id')
    if responsavel == 'sem':
        condicoes.append('responsavel_id IS NULL')
    elif responsavel:
        valor = _inteiro('responsavel_id', None, 1, 2147483647, campos)
        if valor is not None:
            condicoes.append('responsavel_id = ?')
            params.append(valor)
        elif 'responsavel_id' in campos:
            campos['responsavel_id'] = 'Use um id numérico ou "sem" (demandas sem responsável).'

    solicitante = _inteiro('solicitante_id', None, 1, 2147483647, campos)
    if solicitante is not None:
        condicoes.append('solicitante_id = ?')
        params.append(solicitante)

    busca = _texto('busca')
    if len(busca) > 100:
        campos['busca'] = 'Máximo de 100 caracteres.'
    elif busca:
        condicoes.append("(titulo LIKE ? ESCAPE '\\' OR solicitante LIKE ? ESCAPE '\\')")
        params += [f'%{_escapar_like(busca)}%'] * 2

    criada_de = _data('criada_de', campos)
    if criada_de:
        condicoes.append('date(data_criacao) >= ?')
        params.append(criada_de)
    criada_ate = _data('criada_ate', campos)
    if criada_ate:
        condicoes.append('date(data_criacao) <= ?')
        params.append(criada_ate)

    ordenar = _texto('ordenar') or 'prioridade'
    if ordenar not in ORDENACOES:
        campos['ordenar'] = 'Use um destes valores: ' + ', '.join(ORDENACOES) + '.'
        ordenar = 'prioridade'

    pagina, por_pagina = _ler_paginacao(campos)

    if campos:
        raise ErroRegra(VALIDACAO, 'validacao', 'Parâmetros de consulta inválidos.', campos)
    return ' AND '.join(condicoes), params, ordenar, pagina, por_pagina


# ---------------------------------------------------------------- DEMANDAS: LEITURA

@api_bp.route('/demandas', methods=['GET'])
@requer_token_api('visualizar')
def listar_demandas():
    """Filtros: status, prioridade, responsavel_id (id ou "sem"), solicitante_id, busca,
    criada_de, criada_ate, ordenar (solicitante|data|prioridade|prazo), pagina, por_pagina (máx. 100)."""
    where, params, ordenar, pagina, por_pagina = _filtros_demandas()
    with transacao() as cursor:
        total = cursor.execute(
            f"SELECT COUNT(*) FROM {BASE_DEMANDAS} WHERE {where}", params).fetchone()[0]
        linhas = cursor.execute(
            f"SELECT * FROM {BASE_DEMANDAS} WHERE {where} "
            f"ORDER BY {ORDENACOES[ordenar]}, id ASC LIMIT ? OFFSET ?",
            params + [por_pagina, (pagina - 1) * por_pagina]).fetchall()
    return ok([serializar_demanda(l) for l in linhas],
              paginacao=_paginacao(total, pagina, por_pagina))


@api_bp.route(f'/demandas/{ID}', methods=['GET'])
@requer_token_api('visualizar')
def obter_demanda(id):
    with transacao() as cursor:
        return ok(serializar_demanda(_obter_demanda(cursor, id)))


@api_bp.route(f'/demandas/{ID}/comentarios', methods=['GET'])
@requer_token_api('visualizar')
def listar_comentarios(id):
    campos = {}
    pagina, por_pagina = _ler_paginacao(campos)
    if campos:
        raise ErroRegra(VALIDACAO, 'validacao', 'Parâmetros de consulta inválidos.', campos)
    with transacao() as cursor:
        _obter_demanda(cursor, id)
        total = cursor.execute(
            "SELECT COUNT(*) FROM comentarios WHERE demanda_id = ?", (id,)).fetchone()[0]
        linhas = cursor.execute(
            "SELECT * FROM comentarios WHERE demanda_id = ? ORDER BY id LIMIT ? OFFSET ?",
            (id, por_pagina, (pagina - 1) * por_pagina)).fetchall()
    return ok([serializar_comentario(l) for l in linhas],
              paginacao=_paginacao(total, pagina, por_pagina))


@api_bp.route('/dashboard', methods=['GET'])
@requer_token_api('ver_painel')
def dashboard():
    """Mesmos números e filtros do dashboard das telas (data_inicio, data_fim, responsavel,
    prioridade, status, visao). Valores inválidos são ignorados; `filtros` mostra o que valeu."""
    filtros = ler_filtros_dashboard(request.args)
    conn = get_db()
    try:
        dados = calcular_dashboard(conn, filtros)
    finally:
        conn.close()
    dados['filtros'] = filtros
    return ok(dados)


@api_bp.route('/logs', methods=['GET'])
@requer_token_api('ver_logs')
def listar_logs():
    campos = {}
    acao = _texto('acao')
    if acao and acao not in ACOES_LOG:
        campos['acao'] = 'Use uma destas ações: ' + ', '.join(ACOES_LOG) + '.'
    pagina, por_pagina = _ler_paginacao(campos)
    if campos:
        raise ErroRegra(VALIDACAO, 'validacao', 'Parâmetros de consulta inválidos.', campos)
    where, params = ('acao = ?', [acao]) if acao else ('1=1', [])
    with transacao() as cursor:
        total = cursor.execute(f"SELECT COUNT(*) FROM logs WHERE {where}", params).fetchone()[0]
        linhas = cursor.execute(
            f"SELECT * FROM logs WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [por_pagina, (pagina - 1) * por_pagina]).fetchall()
    return ok([serializar_log(l) for l in linhas],
              paginacao=_paginacao(total, pagina, por_pagina))


# ---------------------------------------------------------------- DEMANDAS: ESCRITA

@api_bp.route('/demandas', methods=['POST'])
@requer_token_api('criar_demanda')
def criar_demanda():
    """Corpo: titulo, descricao, prioridade, prazo (AAAA-MM-DD). O solicitante é o dono do token."""
    dados = _corpo_json()
    with transacao() as cursor:
        novo_id = servicos.criar_demanda(cursor, g.usuario_api, dados,
                                         titulo_sem_pontuacao=TITULO_SEM_PONTUACAO_API)
        demanda = _obter_demanda(cursor, novo_id)
    resposta = ok(serializar_demanda(demanda), 201)
    resposta.headers['Location'] = url_for('api.obter_demanda', id=novo_id)
    return resposta


@api_bp.route(f'/demandas/{ID}', methods=['PATCH'])
@requer_token_api('editar_demanda')
def editar_demanda(id):
    """Edição parcial: envie só os campos que mudam (titulo, descricao, prioridade, prazo)."""
    dados = _corpo_json()
    with transacao() as cursor:
        servicos.editar_demanda(cursor, id, dados, titulo_sem_pontuacao=TITULO_SEM_PONTUACAO_API)
        return ok(serializar_demanda(_obter_demanda(cursor, id)))


@api_bp.route(f'/demandas/{ID}', methods=['DELETE'])
@requer_token_api('excluir_demanda')
def excluir_demanda(id):
    with transacao() as cursor:
        servicos.excluir_demanda(cursor, id)
    return sem_conteudo()


def _acao_de_status(servico, id):
    with transacao() as cursor:
        servico(cursor, id, g.usuario_api)
        return ok(serializar_demanda(_obter_demanda(cursor, id)))


@api_bp.route(f'/demandas/{ID}/responsavel', methods=['POST'])
@requer_token_api('alterar_status')
def assumir_responsabilidade(id):
    """O dono do token passa a ser o responsável (sem corpo). 409 se já houver responsável."""
    return _acao_de_status(servicos.assumir_responsabilidade, id)


@api_bp.route(f'/demandas/{ID}/concluir', methods=['POST'])
@requer_token_api('alterar_status')
def concluir_demanda(id):
    """Só o responsável conclui (403 caso contrário). 409 se já estiver finalizada."""
    return _acao_de_status(servicos.concluir_demanda, id)


@api_bp.route(f'/demandas/{ID}/cancelar', methods=['POST'])
@requer_token_api('alterar_status')
def cancelar_demanda(id):
    return _acao_de_status(servicos.cancelar_demanda, id)


@api_bp.route(f'/demandas/{ID}/comentarios', methods=['POST'])
@requer_token_api('comentar')
def criar_comentario(id):
    """Corpo: {"comentario": "..."} (até 1000 caracteres). O autor é o dono do token."""
    dados = _corpo_json()
    with transacao() as cursor:
        _obter_demanda(cursor, id)
        novo_id = servicos.adicionar_comentario(cursor, id, g.usuario_api, dados.get('comentario'))
        comentario = cursor.execute("SELECT * FROM comentarios WHERE id = ?", (novo_id,)).fetchone()
    return ok(serializar_comentario(comentario), 201)