"""
Núcleo compartilhado do SGDI.

Tudo que as telas HTML (app.py) e a API REST (api.py) usam em comum fica aqui:
constantes, perfis/permissões, acesso ao banco e auditoria.

IMPORTANTE: este módulo NUNCA importa app.py nem api.py. Como o projeto roda
com `python app.py`, o app.py executa como __main__; se outro arquivo fizesse
`from app import ...`, o Python importaria o app.py de novo e criaria uma
SEGUNDA aplicação Flask. Por isso o código compartilhado mora aqui.
"""
import hashlib
import os
import sqlite3
import string
from datetime import datetime

from flask import g, request, session

STATUS_FINAIS = ('Concluído', 'Cancelado')

# Ações que podem aparecer na tela de logs (também usadas no filtro)
ACOES_LOG = (
    'LOGIN', 'LOGIN_FALHA', 'LOGOUT',
    'CRIAR_DEMANDA', 'EDITAR_DEMANDA', 'EXCLUIR_DEMANDA',
    'ASSUMIR_RESPONSABILIDADE', 'CONCLUIR_DEMANDA', 'CANCELAR_DEMANDA',
    'CRIAR_COMENTARIO', 'INICIALIZAR_BANCO', 'EXPORTAR_RELATORIO',
    'ACESSO_NEGADO',
    # API REST
    'API_LOGIN', 'API_LOGIN_FALHA', 'API_LOGOUT',
)

# ---- Perfis e permissões ----
# O perfil de cada pessoa fica na coluna solicitantes.perfil. No login o e-mail
# identifica a conta e a conta define o perfil. Para criar um perfil novo
# (ex.: 'gestor') basta adicionar uma entrada em PERMISSOES_POR_PERFIL e em
# ROTULOS_PERFIL; para uma permissão nova, adicione o nome aqui e proteja a
# rota com @requer_permissao('nome') (telas) ou @requer_token_api('nome') (API),
# e os botões com {% if pode('nome') %}.
PERFIL_USUARIO = 'usuario'
PERFIL_ADMIN = 'admin'
PERFIL_INTEGRACAO = 'integracao'   # conta de serviço para sistemas externos

PERMISSOES_POR_PERFIL = {
    PERFIL_USUARIO: {
        'visualizar', 'criar_demanda', 'comentar',
    },
    PERFIL_ADMIN: {
        'visualizar', 'criar_demanda', 'comentar',
        'editar_demanda', 'excluir_demanda', 'alterar_status',
        'ver_logs', 'ver_painel',
    },
    # Só o necessário: sistemas externos não editam/excluem nem leem os logs.
    PERFIL_INTEGRACAO: {
        'visualizar', 'criar_demanda', 'comentar', 'alterar_status',
    },
}

ROTULOS_PERFIL = {
    PERFIL_USUARIO: 'Usuário',
    PERFIL_ADMIN: 'Administrador',
    PERFIL_INTEGRACAO: 'Integração',
}

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

# ---- Tokens da API ----
# Só o hash SHA-256 do token é guardado: se o banco vazar, os tokens não vazam.
SQL_TABELA_API_TOKENS = '''
CREATE TABLE IF NOT EXISTS api_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash TEXT NOT NULL UNIQUE,
    usuario_id INTEGER NOT NULL,
    criado_em TEXT NOT NULL,
    expira_em TEXT NOT NULL,
    revogado_em TEXT,
    ip TEXT,
    FOREIGN KEY (usuario_id) REFERENCES solicitantes (id)
)
'''


def _ler_horas_token():
    try:
        horas = float(os.environ.get('SGDI_API_TOKEN_HORAS', '12'))
    except ValueError:
        horas = 12.0
    return horas if horas > 0 else 12.0


API_TOKEN_HORAS = _ler_horas_token()   # validade do token (padrão 12h)

# Título sem pontuação: regra das telas desde sempre. Na API pode ser relaxada
# (sistemas externos mandam hífen, vírgula...) com SGDI_API_TITULO_SEM_PONTUACAO=0.
TITULO_SEM_PONTUACAO_API = os.environ.get('SGDI_API_TITULO_SEM_PONTUACAO', '1') != '0'


# ---------------------------------------------------------------- BANCO

def caminho_banco():
    # Lido a cada chamada (e não no import) para os testes poderem usar outro arquivo.
    return os.environ.get('SGDI_DB', 'demandas.db')


def get_db():
    conn = sqlite3.connect(caminho_banco())
    conn.row_factory = sqlite3.Row
    return conn


def garantir_tabela_tokens(conn):
    """Cria api_tokens se faltar (bancos criados antes da API não precisam do init_db)."""
    conn.execute(SQL_TABELA_API_TOKENS)
    conn.commit()


# ---------------------------------------------------------------- UTILITÁRIOS

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


def hash_token(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def requisicao_api():
    """True se a requisição atual é para a API (/api/...)."""
    return request.path == '/api' or request.path.startswith('/api/')


# ---------------------------------------------------------------- USUÁRIO ATUAL

def usuario_atual():
    """
    Quem está agindo agora, venha de onde vier:
      - API: o usuário do token (api.py preenche g.usuario_api);
      - telas: o usuário da sessão.
    Devolve um dict com id, nome, email e perfil, ou None se ninguém estiver logado.
    """
    usuario_api = g.get('usuario_api')
    if usuario_api:
        return usuario_api
    if 'usuario_id' in session:
        return {
            'id': session.get('usuario_id'),
            'nome': session.get('usuario'),
            'email': session.get('email'),
            'perfil': session.get('perfil'),
        }
    return None


def perfil_atual():
    """Perfil do usuário atual. Perfil ausente ou desconhecido = o de menos privilégios."""
    usuario = usuario_atual()
    perfil = usuario.get('perfil') if usuario else None
    return perfil if perfil in PERMISSOES_POR_PERFIL else PERFIL_USUARIO


def pode(permissao):
    return permissao in PERMISSOES_POR_PERFIL[perfil_atual()]


# ---------------------------------------------------------------- AUDITORIA

def registrar_log(cursor, acao, entidade, entidade_id, detalhes=None,
                  usuario_id=None, usuario_nome=None):
    """
    Grava um registro de auditoria usando o MESMO cursor da operação: o commit
    da rota grava a ação e o log juntos (ou nenhum dos dois).
    Se usuario_id/usuario_nome não forem informados, usa o usuário atual
    (sessão nas telas, token na API).
    Requisições da API ganham "origem=api" nos detalhes automaticamente.
    """
    usuario = usuario_atual() or {}
    if g.get('origem') == 'api':
        detalhes = f"{detalhes}; origem=api" if detalhes else 'origem=api'
    cursor.execute(
        """INSERT INTO logs (data_hora, usuario_id, usuario_nome, acao,
                             entidade, entidade_id, detalhes, ip)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            agora(),
            usuario_id if usuario_id is not None else usuario.get('id'),
            usuario_nome if usuario_nome is not None else usuario.get('nome'),
            acao, entidade, entidade_id, detalhes, request.remote_addr,
        )
    )


# ---------------------------------------------------------------- ORDENAÇÃO E DASHBOARD
# Usados pelas telas (app.py) e pela API (api.py): uma única fonte para as duas.

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