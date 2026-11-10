"""
Testes das fases 3 e 4 da API (leitura e escrita) + regressão das telas HTML.
Rodar com:  python -m unittest test_api_crud -v
(ou tudo:   python -m unittest -v)
"""
import unittest
from unittest import mock

import servicos
from core import get_db
from test_api import ADMIN, INTEGRACAO, USUARIO, BaseApi, sgdi_app

NOVA = {'titulo': 'Integrar com o ERP', 'descricao': 'Enviar demandas para o ERP',
        'prioridade': 'Alta', 'prazo': '2030-12-31'}


class BaseCrud(BaseApi):
    def setUp(self):
        self.t_admin = self.login(ADMIN)
        self.t_user = self.login(USUARIO)
        self.t_int = self.login(INTEGRACAO)

    def req(self, metodo, url, token=None, **kw):
        headers = self.cab(token) if token else {}
        return getattr(self.client, metodo)('/api/v1' + url, headers=headers, **kw)

    def criar(self, token=None, **extra):
        resp = self.req('post', '/demandas', token or self.t_user, json={**NOVA, **extra})
        self.assertEqual(resp.status_code, 201, resp.get_data(as_text=True))
        return resp.get_json()['dados']


# ================================================================ LEITURA

class TestLeitura(BaseCrud):
    def test_exige_token(self):
        for url in ('/demandas', '/demandas/2', '/demandas/2/comentarios', '/dashboard', '/logs'):
            self.assertErro(self.req('get', url), 401, 'nao_autenticado')

    def test_listar_formato_e_campos(self):
        resp = self.req('get', '/demandas', self.t_user)
        self.assertEqual(resp.status_code, 200)
        corpo = resp.get_json()
        self.assertEqual(set(corpo), {'dados', 'paginacao'})
        self.assertEqual(corpo['paginacao']['pagina'], 1)
        self.assertEqual(corpo['paginacao']['por_pagina'], 20)
        self.assertEqual(corpo['paginacao']['total'], 15)
        self.assertEqual(len(corpo['dados']), 15)
        self.assertEqual(set(corpo['dados'][0]), set(__import__('api').CAMPOS_DEMANDA))

    def test_paginacao(self):
        p1 = self.req('get', '/demandas?por_pagina=6&pagina=1', self.t_user).get_json()
        p3 = self.req('get', '/demandas?por_pagina=6&pagina=3', self.t_user).get_json()
        self.assertEqual(p1['paginacao']['total_paginas'], 3)
        self.assertEqual(len(p1['dados']), 6)
        self.assertEqual(len(p3['dados']), 3)
        ids = [d['id'] for d in p1['dados']] + [d['id'] for d in
               self.req('get', '/demandas?por_pagina=6&pagina=2', self.t_user).get_json()['dados']] + \
              [d['id'] for d in p3['dados']]
        self.assertEqual(len(set(ids)), 15)    # sem repetição entre páginas
        vazia = self.req('get', '/demandas?por_pagina=6&pagina=99', self.t_user).get_json()
        self.assertEqual(vazia['dados'], [])

    def test_parametros_invalidos_da_422(self):
        for consulta, campo in [('por_pagina=1000', 'por_pagina'), ('por_pagina=0', 'por_pagina'),
                                ('pagina=abc', 'pagina'), ('status=Qualquer', 'status'),
                                ('prioridade=X', 'prioridade'), ('ordenar=senha', 'ordenar'),
                                ('criada_de=2024-13-45', 'criada_de'), ('solicitante_id=x', 'solicitante_id'),
                                ('responsavel_id=x', 'responsavel_id')]:
            resp = self.req('get', '/demandas?' + consulta, self.t_user)
            self.assertErro(resp, 422, 'validacao')
            self.assertIn(campo, resp.get_json()['erro']['campos'], consulta)

    def test_filtros(self):
        def listar(q):
            return self.req('get', '/demandas?' + q, self.t_user).get_json()['dados']
        self.assertTrue(all(d['prioridade'] == 'Urgente' for d in listar('prioridade=Urgente')))
        self.assertEqual({d['id'] for d in listar('status=Concluído')}, {1})
        self.assertEqual({d['id'] for d in listar('status=Cancelado')}, {9})
        self.assertEqual({d['id'] for d in listar('responsavel_id=4')}, {1})
        self.assertEqual(len(listar('responsavel_id=sem')), 14)
        self.assertTrue(all(d['solicitante_id'] == 4 for d in listar('solicitante_id=4')))
        self.assertEqual({d['id'] for d in listar('busca=bug no login')}, {1})
        self.assertTrue(listar('busca=maria'))                      # também busca no solicitante
        self.assertEqual(listar('busca=100%'), [])                  # % não vira curinga
        self.assertEqual(len(listar('criada_de=2024-01-20&criada_ate=2024-01-22')), 3)
        atrasadas = listar('status=Atrasado')
        self.assertTrue(all(d['status'] == 'Aberto' and d['status_exibido'] == 'Atrasado' for d in atrasadas))

    def test_ordenacao(self):
        prazos = [d['prazo'] for d in self.req('get', '/demandas?ordenar=prazo', self.t_user).get_json()['dados']]
        self.assertEqual(prazos, sorted(prazos))
        prios = [d['prioridade'] for d in self.req('get', '/demandas', self.t_user).get_json()['dados']]
        self.assertEqual(prios[0], 'Urgente')

    def test_detalhe_e_404(self):
        d = self.req('get', '/demandas/1', self.t_user).get_json()['dados']
        self.assertEqual((d['id'], d['status'], d['responsavel']), (1, 'Concluído', 'João Silva'))
        self.assertErro(self.req('get', '/demandas/99999', self.t_user), 404, 'demanda_nao_encontrada')
        self.assertErro(self.req('get', '/demandas/99999999999999999999', self.t_user), 404, 'nao_encontrado')
        self.assertErro(self.req('get', '/demandas/abc', self.t_user), 404, 'nao_encontrado')

    def test_comentarios_listagem(self):
        resp = self.req('get', '/demandas/1/comentarios', self.t_user).get_json()
        self.assertEqual([c['id'] for c in resp['dados']], [1, 2])
        self.assertEqual(set(resp['dados'][0]), {'id', 'demanda_id', 'autor', 'comentario', 'criado_em'})
        self.assertErro(self.req('get', '/demandas/99999/comentarios', self.t_user), 404, 'demanda_nao_encontrada')

    def test_dashboard(self):
        self.assertErro(self.req('get', '/dashboard', self.t_user), 403, 'acesso_negado')
        self.assertErro(self.req('get', '/dashboard', self.t_int), 403, 'acesso_negado')
        d = self.req('get', '/dashboard', self.t_admin).get_json()['dados']
        self.assertEqual(d['total'], 15)
        self.assertEqual(set(d['status']), {'Aberto', 'Atrasado', 'Concluído', 'Cancelado'})
        self.assertEqual(d['status']['Concluído']['n'], 1)
        urgentes = self.req('get', '/dashboard?prioridade=Urgente', self.t_admin).get_json()['dados']
        self.assertEqual(urgentes['filtros']['prioridade'], 'Urgente')
        self.assertEqual(urgentes['total'], len(urgentes['criticas']))

    def test_logs(self):
        self.assertErro(self.req('get', '/logs', self.t_user), 403, 'acesso_negado')
        self.assertErro(self.req('get', '/logs', self.t_int), 403, 'acesso_negado')
        resp = self.req('get', '/logs?acao=API_LOGIN&por_pagina=5', self.t_admin).get_json()
        self.assertTrue(resp['dados'])
        self.assertTrue(all(l['acao'] == 'API_LOGIN' for l in resp['dados']))
        self.assertErro(self.req('get', '/logs?acao=INVENTADA', self.t_admin), 422, 'validacao')


# ================================================================ ESCRITA

class TestCriar(BaseCrud):
    def test_criar_201(self):
        resp = self.req('post', '/demandas', self.t_user, json=NOVA)
        self.assertEqual(resp.status_code, 201)
        d = resp.get_json()['dados']
        self.assertTrue(resp.headers['Location'].endswith(f"/api/v1/demandas/{d['id']}"))
        self.assertEqual((d['status'], d['status_exibido'], d['responsavel']), ('Aberto', 'Aberto', None))
        self.assertEqual((d['solicitante'], d['solicitante_id']), ('João Silva', 4))   # vem do token
        self.assertEqual(self.req('get', f"/demandas/{d['id']}", self.t_user).status_code, 200)
        log = self.sql("SELECT * FROM logs WHERE acao='CRIAR_DEMANDA' AND entidade_id=?", (d['id'],))[0]
        self.assertIn('origem=api', log['detalhes'])
        self.assertEqual(log['usuario_id'], 4)

    def test_integracao_pode_criar(self):
        self.assertEqual(self.criar(self.t_int)['solicitante'], 'Integração SGDI')

    def test_validacao(self):
        casos = [
            ({}, {'titulo', 'descricao', 'prioridade', 'prazo'}),
            ({**NOVA, 'prioridade': 'Crítica'}, {'prioridade'}),
            ({**NOVA, 'prazo': '31/12/2030'}, {'prazo'}),
            ({**NOVA, 'prazo': '2030-02-30'}, {'prazo'}),
            ({**NOVA, 'prazo': '2030-1-5'}, {'prazo'}),
            ({**NOVA, 'titulo': 'Com, pontuação!'}, {'titulo'}),
            ({**NOVA, 'titulo': 'x' * 101}, {'titulo'}),
            ({**NOVA, 'descricao': 'x' * 1001}, {'descricao'}),
            ({**NOVA, 'titulo': '   '}, {'titulo'}),
            ({**NOVA, 'titulo': 123}, {'titulo'}),
            ({**NOVA, 'status': 'Concluído', 'solicitante_id': 1}, {'status', 'solicitante_id'}),
        ]
        for corpo, esperados in casos:
            resp = self.req('post', '/demandas', self.t_user, json=corpo)
            self.assertErro(resp, 422, 'validacao')
            self.assertEqual(set(resp.get_json()['erro']['campos']), esperados, corpo)

    def test_corpo_invalido_400(self):
        self.assertErro(self.req('post', '/demandas', self.t_user, data='x=1'), 400, 'requisicao_invalida')
        self.assertErro(self.req('post', '/demandas', self.t_user, json=[1]), 400, 'requisicao_invalida')

    def test_validacao_nao_grava_nada(self):
        antes = self.sql("SELECT COUNT(*) AS n FROM demandas")[0]['n']
        self.req('post', '/demandas', self.t_user, json={**NOVA, 'prioridade': 'X'})
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM demandas")[0]['n'], antes)

    def test_titulo_com_pontuacao_pode_ser_liberado_na_api(self):
        with mock.patch('api.TITULO_SEM_PONTUACAO_API', False):
            d = self.criar(titulo='ERP - integração, fase 1')
        self.assertEqual(d['titulo'], 'ERP - integração, fase 1')


class TestEditarExcluir(BaseCrud):
    def test_permissoes(self):
        d = self.criar()
        for token in (self.t_user, self.t_int):
            self.assertErro(self.req('patch', f"/demandas/{d['id']}", token, json={'titulo': 'Novo'}), 403, 'acesso_negado')
            self.assertErro(self.req('delete', f"/demandas/{d['id']}", token), 403, 'acesso_negado')

    def test_patch_parcial_e_log(self):
        d = self.criar()
        resp = self.req('patch', f"/demandas/{d['id']}", self.t_admin, json={'prioridade': 'Urgente', 'titulo': 'Novo titulo'})
        self.assertEqual(resp.status_code, 200)
        novo = resp.get_json()['dados']
        self.assertEqual((novo['prioridade'], novo['titulo'], novo['descricao']),
                         ('Urgente', 'Novo titulo', NOVA['descricao']))
        log = self.sql("SELECT detalhes FROM logs WHERE acao='EDITAR_DEMANDA' AND entidade_id=?", (d['id'],))[0]['detalhes']
        self.assertIn("prioridade: 'Alta' -> 'Urgente'", log)
        self.assertIn('origem=api', log)

    def test_patch_sem_mudanca_nao_loga(self):
        d = self.criar()
        self.req('patch', f"/demandas/{d['id']}", self.t_admin, json={'prioridade': 'Alta'})
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM logs WHERE acao='EDITAR_DEMANDA' AND entidade_id=?",
                                  (d['id'],))[0]['n'], 0)

    def test_patch_validacoes(self):
        d = self.criar()
        url = f"/demandas/{d['id']}"
        self.assertErro(self.req('patch', url, self.t_admin, json={}), 422, 'validacao')
        for corpo, campo in [({'status': 'Concluído'}, 'status'), ({'id': 99}, 'id'),
                             ({'prazo': 'amanhã'}, 'prazo'), ({'prioridade': 'Z'}, 'prioridade')]:
            resp = self.req('patch', url, self.t_admin, json=corpo)
            self.assertErro(resp, 422, 'validacao')
            self.assertIn(campo, resp.get_json()['erro']['campos'])
        self.assertErro(self.req('patch', '/demandas/99999', self.t_admin, json={'titulo': 'A'}), 404, 'demanda_nao_encontrada')

    def test_delete_remove_demanda_e_comentarios(self):
        d = self.criar()
        self.req('post', f"/demandas/{d['id']}/comentarios", self.t_user, json={'comentario': 'oi'})
        resp = self.req('delete', f"/demandas/{d['id']}", self.t_admin)
        self.assertEqual(resp.status_code, 204)
        self.assertErro(self.req('get', f"/demandas/{d['id']}", self.t_user), 404, 'demanda_nao_encontrada')
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM comentarios WHERE demanda_id=?", (d['id'],))[0]['n'], 0)
        self.assertTrue(self.sql("SELECT 1 FROM logs WHERE acao='EXCLUIR_DEMANDA' AND entidade_id=? "
                                 "AND detalhes LIKE '%comentarios_removidos=1%'", (d['id'],)))
        self.assertErro(self.req('delete', f"/demandas/{d['id']}", self.t_admin), 404, 'demanda_nao_encontrada')


class TestFluxoDeStatus(BaseCrud):
    def test_fluxo_completo(self):
        d = self.criar()
        url = f"/demandas/{d['id']}"
        # sem permissão
        self.assertErro(self.req('post', url + '/responsavel', self.t_user), 403, 'acesso_negado')
        # não é o responsável: ainda não há responsável
        self.assertErro(self.req('post', url + '/concluir', self.t_admin), 403, 'nao_e_responsavel')
        # integração assume
        resp = self.req('post', url + '/responsavel', self.t_int)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()['dados']['responsavel'], 'Integração SGDI')
        # já tem responsável (inclusive para quem já é o responsável)
        self.assertErro(self.req('post', url + '/responsavel', self.t_admin), 409, 'ja_tem_responsavel')
        self.assertErro(self.req('post', url + '/responsavel', self.t_int), 409, 'ja_tem_responsavel')
        # outro admin não conclui
        self.assertErro(self.req('post', url + '/concluir', self.t_admin), 403, 'nao_e_responsavel')
        # responsável conclui
        resp = self.req('post', url + '/concluir', self.t_int)
        self.assertEqual(resp.status_code, 200)
        c = resp.get_json()['dados']
        self.assertEqual((c['status'], c['status_exibido']), ('Concluído', 'Concluído'))
        self.assertTrue(c['data_conclusao'])
        # finalizada: nada mais muda
        for acao in ('concluir', 'cancelar', 'responsavel'):
            self.assertErro(self.req('post', f'{url}/{acao}', self.t_int if acao != 'cancelar' else self.t_admin),
                            409, 'demanda_finalizada')
        for acao in ('CONCLUIR_DEMANDA', 'ASSUMIR_RESPONSABILIDADE'):
            self.assertTrue(self.sql("SELECT 1 FROM logs WHERE acao=? AND entidade_id=? AND detalhes LIKE '%origem=api%'",
                                     (acao, d['id'])))

    def test_cancelar(self):
        d = self.criar()
        url = f"/demandas/{d['id']}/cancelar"
        self.assertErro(self.req('post', url, self.t_user), 403, 'acesso_negado')
        resp = self.req('post', url, self.t_admin)
        self.assertEqual(resp.get_json()['dados']['status'], 'Cancelado')
        self.assertErro(self.req('post', url, self.t_admin), 409, 'demanda_finalizada')
        self.assertErro(self.req('post', f"/demandas/{d['id']}/responsavel", self.t_admin), 409, 'demanda_finalizada')

    def test_404_nas_acoes(self):
        for acao in ('responsavel', 'concluir', 'cancelar'):
            self.assertErro(self.req('post', f'/demandas/99999/{acao}', self.t_admin), 404, 'demanda_nao_encontrada')

    def test_concorrencia_assumir(self):
        """Dois clientes leem 'sem responsável' ao mesmo tempo: só o primeiro pode vencer."""
        d = self.criar()
        conn = get_db()
        obsoleta = conn.execute('SELECT * FROM demandas WHERE id=?', (d['id'],)).fetchone()
        conn.close()
        self.assertEqual(self.req('post', f"/demandas/{d['id']}/responsavel", self.t_admin).status_code, 200)

        original = servicos._obter
        chamadas = []

        def obter_com_leitura_obsoleta(cursor, id):
            chamadas.append(id)
            return obsoleta if len(chamadas) == 1 else original(cursor, id)

        conn = get_db()
        try:
            with mock.patch.object(servicos, '_obter', obter_com_leitura_obsoleta):
                with self.assertRaises(servicos.ErroRegra) as ctx:
                    with sgdi_app.test_request_context():
                        servicos.assumir_responsabilidade(conn.cursor(), d['id'],
                                                          {'id': 5, 'nome': 'Maria Santos'})
        finally:
            conn.close()
        self.assertEqual(ctx.exception.codigo, 'ja_tem_responsavel')
        self.assertEqual(len(chamadas), 2)   # 1ª leitura obsoleta passou; o UPDATE condicional barrou; 2ª leitura explicou
        resp = self.req('get', f"/demandas/{d['id']}", self.t_user).get_json()['dados']
        self.assertEqual(resp['responsavel'], 'Juliana Castanho Teixeira')   # o primeiro continua


class TestComentarios(BaseCrud):
    def test_criar_e_listar(self):
        d = self.criar()
        url = f"/demandas/{d['id']}/comentarios"
        resp = self.req('post', url, self.t_user, json={'comentario': '  Primeiro comentário  '})
        self.assertEqual(resp.status_code, 201)
        c = resp.get_json()['dados']
        self.assertEqual((c['autor'], c['comentario']), ('João Silva (#4)', 'Primeiro comentário'))
        lista = self.req('get', url, self.t_int).get_json()['dados']
        self.assertEqual([x['comentario'] for x in lista], ['Primeiro comentário'])
        log = self.sql("SELECT detalhes FROM logs WHERE acao='CRIAR_COMENTARIO' AND entidade_id=?", (c['id'],))[0]
        self.assertIn('origem=api', log['detalhes'])

    def test_validacao(self):
        d = self.criar()
        url = f"/demandas/{d['id']}/comentarios"
        for corpo in ({'comentario': ''}, {'comentario': '   '}, {}, {'comentario': 5}, {'comentario': 'x' * 1001}):
            resp = self.req('post', url, self.t_user, json=corpo)
            self.assertErro(resp, 422, 'validacao')
            self.assertIn('comentario', resp.get_json()['erro']['campos'])
        self.assertEqual(self.req('post', url, self.t_user, json={'comentario': 'x' * 1000}).status_code, 201)
        self.assertErro(self.req('post', '/demandas/99999/comentarios', self.t_user, json={'comentario': 'a'}),
                        404, 'demanda_nao_encontrada')


# ================================================================ TELAS HTML (regressão)

class TestTelasContinuamFuncionando(BaseCrud):
    def setUp(self):
        super().setUp()
        self.web = sgdi_app.test_client()
        self.web.post('/login', data={'email': ADMIN['email'], 'senha': ADMIN['senha']})

    def test_paginas_abrem(self):
        for rota in ('/', '/detalhes/2', '/nova_demanda', '/editar/2', '/dashboard', '/dashboard/dados',
                     '/dashboard/exportar', '/dashboard/relatorio', '/logs'):
            self.assertEqual(self.web.get(rota).status_code, 200, rota)

    def test_criar_editar_e_acoes_pelo_formulario(self):
        resp = self.web.post('/nova_demanda', data=NOVA)
        self.assertEqual(resp.status_code, 302)
        nova = self.sql("SELECT * FROM demandas ORDER BY id DESC LIMIT 1")[0]
        self.assertEqual((nova['titulo'], nova['solicitante']), (NOVA['titulo'], 'Juliana Castanho Teixeira'))
        i = nova['id']
        self.web.post(f'/editar/{i}', data={**NOVA, 'titulo': 'Titulo editado'})
        self.assertEqual(self.sql("SELECT titulo FROM demandas WHERE id=?", (i,))[0]['titulo'], 'Titulo editado')
        self.web.post(f'/responsavel/{i}')
        self.assertEqual(self.sql("SELECT responsavel_id FROM demandas WHERE id=?", (i,))[0]['responsavel_id'], 1)
        self.web.post(f'/adicionar_comentario/{i}', data={'comentario': 'pela tela'})
        self.assertEqual(self.sql("SELECT autor FROM comentarios WHERE demanda_id=?", (i,))[0]['autor'],
                         'Juliana Castanho Teixeira (#1)')
        self.web.post(f'/concluir/{i}')
        self.assertEqual(self.sql("SELECT status FROM demandas WHERE id=?", (i,))[0]['status'], 'Concluído')
        self.web.post(f'/cancelar/{i}')      # finalizada: não cancela
        self.assertEqual(self.sql("SELECT status FROM demandas WHERE id=?", (i,))[0]['status'], 'Concluído')
        self.web.post(f'/deletar/{i}')
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM demandas WHERE id=?", (i,))[0]['n'], 0)
        # logs das telas NÃO levam "origem=api"
        self.assertFalse(self.sql("SELECT 1 FROM logs WHERE acao='CRIAR_COMENTARIO' AND detalhes LIKE '%origem=api%' "
                                  "AND detalhes LIKE '%pela tela%'"))

    def test_formulario_invalido_mostra_aviso_e_nao_grava(self):
        antes = self.sql("SELECT COUNT(*) AS n FROM demandas")[0]['n']
        resp = self.web.post('/nova_demanda', data={**NOVA, 'titulo': 'Com, pontuação!'}, follow_redirects=True)
        self.assertIn('caracteres especiais', resp.get_data(as_text=True))
        self.web.post('/nova_demanda', data={**NOVA, 'prioridade': 'Inventada'})
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM demandas")[0]['n'], antes)

    def test_demanda_inexistente_na_tela(self):
        resp = self.web.post('/cancelar/99999', follow_redirects=True)
        self.assertIn('Demanda não encontrada.', resp.get_data(as_text=True))
        self.assertEqual(self.web.get('/editar/99999').status_code, 302)


if __name__ == '__main__':
    unittest.main()