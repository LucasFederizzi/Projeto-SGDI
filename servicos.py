"""
Regras de negócio do SGDI, compartilhadas entre as telas (app.py) e a API (api.py).

Cada função recebe um cursor (e o usuário, quando precisa), valida, grava a
alteração + o log de auditoria no MESMO cursor e NÃO faz commit: quem chama
decide (commit se deu certo, descarta se levantou ErroRegra). Assim as telas e a
API aplicam exatamente as mesmas regras, e só mudam na forma de responder
(flash/redirect de um lado, JSON/HTTP do outro).

Quando uma regra é violada, a função levanta ErroRegra. O campo `tipo` diz a
categoria (a API converte em HTTP; as telas, em flash):
    nao_encontrado -> 404      validacao -> 422      conflito -> 409
    proibido -> 403            requisicao_invalida -> 400

Este módulo importa apenas de core.py.
"""
import re
from datetime import datetime

from core import (PRIORIDADES, STATUS_FINAIS, agora, caracteres_invalidos,
                  registrar_log, resumo)

NAO_ENCONTRADO = 'nao_encontrado'
VALIDACAO = 'validacao'
CONFLITO = 'conflito'
PROIBIDO = 'proibido'
REQUISICAO_INVALIDA = 'requisicao_invalida'

LIMITE_TITULO = 100
LIMITE_DESCRICAO = 1000
LIMITE_COMENTARIO = 1000

CAMPOS_EDITAVEIS = ('titulo', 'descricao', 'prioridade', 'prazo')
ROTULOS_CAMPOS = {'titulo': 'Título', 'descricao': 'Descrição', 'prioridade': 'Prioridade',
                  'prazo': 'Prazo', 'comentario': 'Comentário'}

_FORMATO_PRAZO = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_NAO_FINAL_SQL = 'status NOT IN (' + ', '.join('?' for _ in STATUS_FINAIS) + ')'

MSG_FINALIZADA = 'Essa demanda já está finalizada.'
MSG_CONCORRENCIA = 'A demanda foi alterada por outra requisição. Tente novamente.'


class ErroRegra(Exception):
    def __init__(self, tipo, codigo, mensagem, campos=None):
        super().__init__(mensagem)
        self.tipo = tipo
        self.codigo = codigo
        self.mensagem = mensagem
        self.campos = campos or {}

    def texto_para_tela(self):
        """Mensagem para o flash das telas (junta os erros por campo, se houver)."""
        if self.campos:
            return '; '.join(f"{ROTULOS_CAMPOS.get(c, c)}: {m}" for c, m in self.campos.items())
        return self.mensagem


# ---------------------------------------------------------------- VALIDAÇÃO

def _problema_no_campo(nome, valor, titulo_sem_pontuacao):
    """Devolve a mensagem de erro do campo (já sem espaços nas pontas) ou None se estiver ok."""
    if nome == 'titulo':
        if len(valor) > LIMITE_TITULO:
            return f'Máximo de {LIMITE_TITULO} caracteres.'
        if titulo_sem_pontuacao and caracteres_invalidos(valor):
            return 'Não pode conter caracteres especiais (pontuação).'
    elif nome == 'descricao':
        if len(valor) > LIMITE_DESCRICAO:
            return f'Máximo de {LIMITE_DESCRICAO} caracteres.'
    elif nome == 'prioridade':
        if valor not in PRIORIDADES:
            return 'Use um destes valores: ' + ', '.join(PRIORIDADES) + '.'
    elif nome == 'prazo':
        try:
            if not _FORMATO_PRAZO.match(valor):
                raise ValueError
            datetime.strptime(valor, '%Y-%m-%d')
        except ValueError:
            return 'Use o formato AAAA-MM-DD com uma data válida.'
    return None


def validar_dados_demanda(dados, parcial=False, titulo_sem_pontuacao=True):
    """
    Valida os campos de uma demanda e devolve um dict limpo (textos sem espaços nas pontas).
    parcial=False (criação): os 4 campos são obrigatórios.
    parcial=True (edição): só valida os campos enviados, mas exige ao menos um.
    Campos fora de CAMPOS_EDITAVEIS (id, status, solicitante...) são rejeitados em vez de ignorados.
    """
    campos, limpos = {}, {}
    for nome in dados:
        if nome not in CAMPOS_EDITAVEIS:
            campos[nome] = 'Campo desconhecido ou somente leitura.'

    if parcial and not dados:
        raise ErroRegra(VALIDACAO, 'validacao',
                        'Informe ao menos um campo para alterar: ' + ', '.join(CAMPOS_EDITAVEIS) + '.')

    for nome in CAMPOS_EDITAVEIS:
        if nome not in dados:
            if not parcial:
                campos[nome] = 'Campo obrigatório.'
            continue
        valor = dados[nome]
        if not isinstance(valor, str) or not valor.strip():
            campos[nome] = 'Campo obrigatório (texto não vazio).'
            continue
        valor = valor.strip()
        problema = _problema_no_campo(nome, valor, titulo_sem_pontuacao)
        if problema:
            campos[nome] = problema
        else:
            limpos[nome] = valor

    if campos:
        raise ErroRegra(VALIDACAO, 'validacao', 'Dados inválidos.', campos)
    return limpos


def _obter(cursor, id):
    demanda = cursor.execute('SELECT * FROM demandas WHERE id=?', (id,)).fetchone()
    if demanda is None:
        raise ErroRegra(NAO_ENCONTRADO, 'demanda_nao_encontrada', 'Demanda não encontrada.')
    return demanda


# ---------------------------------------------------------------- CRUD DE DEMANDAS

def criar_demanda(cursor, usuario, dados, titulo_sem_pontuacao=True):
    """Cria a demanda em nome de `usuario` (o solicitante NUNCA vem do corpo). Devolve o id."""
    d = validar_dados_demanda(dados, parcial=False, titulo_sem_pontuacao=titulo_sem_pontuacao)
    cursor.execute(
        "INSERT INTO demandas (titulo, descricao, solicitante, solicitante_id, data_criacao, "
        "prioridade, prazo) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (d['titulo'], d['descricao'], usuario['nome'], usuario['id'], agora(),
         d['prioridade'], d['prazo'])
    )
    novo_id = cursor.lastrowid
    registrar_log(
        cursor, 'CRIAR_DEMANDA', 'demanda', novo_id,
        f"titulo='{resumo(d['titulo'])}'; prioridade={d['prioridade']}; prazo={d['prazo']}; "
        f"descricao='{resumo(d['descricao'])}'"
    )
    return novo_id


def editar_demanda(cursor, id, dados, titulo_sem_pontuacao=True):
    """Altera só os campos enviados. Registra no log apenas o que realmente mudou (antes -> depois)."""
    demanda = _obter(cursor, id)
    novos = validar_dados_demanda(dados, parcial=True, titulo_sem_pontuacao=titulo_sem_pontuacao)
    alterados = {c: v for c, v in novos.items() if demanda[c] != v}
    if not alterados:
        return
    colunas = ', '.join(f'{c}=?' for c in alterados)   # nomes vêm da lista fixa CAMPOS_EDITAVEIS
    cursor.execute(f"UPDATE demandas SET {colunas} WHERE id=?", [*alterados.values(), id])
    mudancas = [f"{c}: '{resumo(demanda[c])}' -> '{resumo(v)}'" for c, v in alterados.items()]
    registrar_log(cursor, 'EDITAR_DEMANDA', 'demanda', id, '; '.join(mudancas))


def excluir_demanda(cursor, id):
    demanda = _obter(cursor, id)
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


# ---------------------------------------------------------------- ASSUMIR / CONCLUIR / CANCELAR

def _aplicar_com_guarda(cursor, id, demanda, checar, sql, parametros):
    """
    Confere a regra, faz o UPDATE condicional e confere o rowcount.
    O WHERE do UPDATE repete a regra, então dois clientes simultâneos não passam juntos:
    só um altera a linha; o outro tem rowcount 0 e recebe o motivo real do erro.
    """
    checar(demanda)
    if cursor.execute(sql, parametros).rowcount == 0:
        checar(_obter(cursor, id))      # alguém mudou a demanda no meio: explica o porquê
        raise ErroRegra(CONFLITO, 'demanda_alterada', MSG_CONCORRENCIA)


def _checar_aberta(demanda, mensagem=MSG_FINALIZADA):
    if demanda['status'] in STATUS_FINAIS:
        raise ErroRegra(CONFLITO, 'demanda_finalizada', mensagem)


def assumir_responsabilidade(cursor, id, usuario):
    def checar(d):
        _checar_aberta(d)
        if d['responsavel_id'] is not None:
            raise ErroRegra(CONFLITO, 'ja_tem_responsavel', 'Essa demanda já tem um responsável.')

    demanda = _obter(cursor, id)
    _aplicar_com_guarda(
        cursor, id, demanda, checar,
        f"UPDATE demandas SET responsavel = ?, responsavel_id = ? "
        f"WHERE id = ? AND responsavel_id IS NULL AND {_NAO_FINAL_SQL}",
        (usuario['nome'], usuario['id'], id, *STATUS_FINAIS)
    )
    registrar_log(cursor, 'ASSUMIR_RESPONSABILIDADE', 'demanda', id,
                  f"responsavel: 'nenhum' -> '{usuario['nome']} (#{usuario['id']})'")


def concluir_demanda(cursor, id, usuario):
    def checar(d):
        _checar_aberta(d)
        if d['responsavel_id'] != usuario['id']:
            raise ErroRegra(PROIBIDO, 'nao_e_responsavel',
                            'Só o responsável pela demanda pode concluí-la.')

    demanda = _obter(cursor, id)
    _aplicar_com_guarda(
        cursor, id, demanda, checar,
        f"UPDATE demandas SET status = 'Concluído', data_conclusao = ? "
        f"WHERE id = ? AND responsavel_id = ? AND {_NAO_FINAL_SQL}",
        (agora(), id, usuario['id'], *STATUS_FINAIS)
    )
    registrar_log(cursor, 'CONCLUIR_DEMANDA', 'demanda', id,
                  f"status: '{demanda['status']}' -> 'Concluído'")


def cancelar_demanda(cursor, id, usuario=None):
    def checar(d):
        _checar_aberta(d, 'Essa demanda já está finalizada e não pode ser cancelada.')

    demanda = _obter(cursor, id)
    _aplicar_com_guarda(
        cursor, id, demanda, checar,
        f"UPDATE demandas SET status = 'Cancelado' WHERE id = ? AND {_NAO_FINAL_SQL}",
        (id, *STATUS_FINAIS)
    )
    registrar_log(cursor, 'CANCELAR_DEMANDA', 'demanda', id,
                  f"status: '{demanda['status']}' -> 'Cancelado'")


# ---------------------------------------------------------------- COMENTÁRIOS

def adicionar_comentario(cursor, demanda_id, usuario, texto):
    """Cria o comentário em nome de `usuario`. Devolve o id do comentário."""
    _obter(cursor, demanda_id)
    if not isinstance(texto, str) or not texto.strip():
        raise ErroRegra(VALIDACAO, 'validacao', 'Dados inválidos.',
                        {'comentario': 'Não pode estar vazio.'})
    texto = texto.strip()
    if len(texto) > LIMITE_COMENTARIO:
        raise ErroRegra(VALIDACAO, 'validacao', 'Dados inválidos.',
                        {'comentario': f'Máximo de {LIMITE_COMENTARIO} caracteres.'})

    autor = f"{usuario['nome']} (#{usuario['id']})"
    cursor.execute(
        "INSERT INTO comentarios (demanda_id, comentario, autor, data) VALUES (?, ?, ?, ?)",
        (demanda_id, texto, autor, agora())
    )
    novo_id = cursor.lastrowid
    registrar_log(cursor, 'CRIAR_COMENTARIO', 'comentario', novo_id,
                  f"demanda_id={demanda_id}; comentario='{texto}'")
    return novo_id