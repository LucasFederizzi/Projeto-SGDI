"""
Testes da documentação (fase 5): a documentação não pode "envelhecer" em relação ao código.
Rodar com:  python -m unittest test_api_docs -v

Os testes marcados com PyYAML são pulados se a biblioteca não estiver instalada
(pip install pyyaml). Os demais não precisam de nada além do Flask.
"""
import os
import re
import unittest

from test_api import BaseApi, PASTA, sgdi_app

try:
    import yaml
except ImportError:          # PyYAML é opcional (só para estes testes extras)
    yaml = None

CAMINHO_YAML = os.path.join(PASTA, 'openapi.yaml')


def texto_openapi():
    with open(CAMINHO_YAML, encoding='utf-8') as arquivo:
        return arquivo.read()


def operacoes_do_yaml(texto):
    """(MÉTODO, caminho) de cada operação em `paths:`, lidos direto do texto (sem PyYAML)."""
    operacoes, dentro, caminho = set(), False, None
    for linha in texto.splitlines():
        if linha.startswith('paths:'):
            dentro = True
            continue
        if dentro and re.match(r'^\S', linha):      # próxima chave de nível 0 (components:)
            break
        achou = re.match(r'^  (/\S*):\s*$', linha)
        if achou:
            caminho = achou.group(1)
            continue
        achou = re.match(r'^    (get|post|put|patch|delete):\s*$', linha)
        if achou and caminho:
            operacoes.add((achou.group(1).upper(), caminho))
    return operacoes


def operacoes_do_codigo():
    operacoes = set()
    for regra in sgdi_app.url_map.iter_rules():
        if not regra.endpoint.startswith('api.'):
            continue
        caminho = re.sub(r'<[^>]*:(\w+)>', r'{\1}', regra.rule[len('/api/v1'):])
        for metodo in regra.methods - {'HEAD', 'OPTIONS'}:
            operacoes.add((metodo, caminho))
    return operacoes


class TestDocumentacao(BaseApi):
    def test_swagger_ui_e_yaml_sao_publicos(self):
        pagina = self.client.get('/api/docs')
        self.assertEqual(pagina.status_code, 200)
        self.assertEqual(pagina.mimetype, 'text/html')
        html = pagina.get_data(as_text=True)
        self.assertIn('SwaggerUIBundle', html)
        self.assertIn('/api/openapi.yaml', html)
        self.assertNotIn('__VERSAO__', html)
        self.assertNotIn('__YAML__', html)

        yml = self.client.get('/api/openapi.yaml')
        self.assertEqual(yml.status_code, 200)
        self.assertIn('yaml', yml.mimetype)
        self.assertTrue(yml.get_data(as_text=True).startswith('openapi: 3'))

    def test_openapi_documenta_exatamente_as_rotas_da_api(self):
        documentadas = operacoes_do_yaml(texto_openapi())
        existentes = operacoes_do_codigo()
        self.assertEqual(existentes - documentadas, set(), 'rota existe no código mas falta no openapi.yaml')
        self.assertEqual(documentadas - existentes, set(), 'rota documentada que não existe no código')
        self.assertEqual(len(existentes), 15)

    def test_todo_codigo_de_erro_do_codigo_esta_documentado(self):
        fontes = ''
        for nome in ('servicos.py', 'api.py'):
            with open(os.path.join(PASTA, nome), encoding='utf-8') as arquivo:
                fontes += arquivo.read()
        codigos = set(re.findall(r"ErroRegra\(\s*[A-Z_]+,\s*'([a-z_]+)'", fontes))
        codigos |= set(re.findall(r"erro\(\s*\d{3},\s*'([a-z_]+)'", fontes))
        codigos |= set(re.findall(r"\d{3}: \('([a-z_]+)',", fontes))
        self.assertGreaterEqual(len(codigos), 12)
        doc = texto_openapi()
        self.assertEqual({c for c in codigos if f'`{c}`' not in doc}, set(),
                         'código de erro usado no código mas ausente da documentação')

    def test_tabela_de_permissoes_da_doc_confere_com_o_codigo(self):
        from core import PERMISSOES_POR_PERFIL
        doc = texto_openapi()
        linhas = {}
        for achou in re.finditer(r'^\s+\| (\w+)[^|]*\|\s*(sim|não)\s*\|\s*(sim|não)\s*\|\s*(sim|não)\s*\|\s*$', doc, re.M):
            linhas[achou.group(1)] = dict(zip(('usuario', 'integracao', 'admin'), achou.groups()[1:]))
        self.assertEqual(len(linhas), 8)
        for permissao, por_perfil in linhas.items():
            for perfil, resposta in por_perfil.items():
                self.assertEqual(permissao in PERMISSOES_POR_PERFIL[perfil], resposta == 'sim',
                                 f'{permissao} / {perfil}')


@unittest.skipIf(yaml is None, 'PyYAML não instalado')
class TestEstruturaDoOpenapi(BaseApi):
    def setUp(self):
        self.spec = yaml.safe_load(texto_openapi())

    def _refs(self, no):
        if isinstance(no, dict):
            for chave, valor in no.items():
                if chave == '$ref':
                    yield valor
                else:
                    yield from self._refs(valor)
        elif isinstance(no, list):
            for item in no:
                yield from self._refs(item)

    def test_refs_resolvem_e_nao_ha_componente_orfao(self):
        usadas = set(self._refs(self.spec))
        for ref in usadas:
            no = self.spec
            for parte in ref.lstrip('#/').split('/'):
                self.assertIn(parte, no, f'$ref quebrada: {ref}')
                no = no[parte]
        todos = {f'#/components/{secao}/{nome}' for secao in ('schemas', 'responses', 'parameters')
                 for nome in self.spec['components'][secao]}
        self.assertEqual(todos - usadas, set())

    def test_operacoes_bem_formadas(self):
        ids = []
        for caminho, item in self.spec['paths'].items():
            for metodo, op in item.items():
                if metodo not in ('get', 'post', 'patch', 'delete'):
                    continue
                ids.append(op['operationId'])
                self.assertTrue(op.get('summary') and op.get('tags'), f'{metodo} {caminho}')
                self.assertIn('responses', op)
                if op.get('security') != []:                 # rota protegida documenta o 401
                    self.assertIn('401', op['responses'], f'{metodo} {caminho}')
                if '{id}' in caminho:                        # toda rota com id documenta o 404
                    self.assertIn('404', op['responses'], f'{metodo} {caminho}')
        self.assertEqual(len(ids), len(set(ids)))

    def test_exemplos_da_documentacao_funcionam_de_verdade(self):
        def exemplo(caminho, metodo):
            return self.spec['paths'][caminho][metodo]['requestBody']['content']['application/json']['example']

        resp = self.client.post('/api/v1/auth/token', json=exemplo('/auth/token', 'post'))
        self.assertEqual(resp.status_code, 200)

        admin = self.login({'email': 'juliana@gmail.com', 'senha': 'juliana'})
        resp = self.client.post('/api/v1/demandas', headers=self.cab(admin), json=exemplo('/demandas', 'post'))
        self.assertEqual(resp.status_code, 201, resp.get_data(as_text=True))
        novo = resp.get_json()['dados']['id']

        resp = self.client.patch(f'/api/v1/demandas/{novo}', headers=self.cab(admin),
                                 json=exemplo('/demandas/{id}', 'patch'))
        self.assertEqual(resp.status_code, 200)
        resp = self.client.post(f'/api/v1/demandas/{novo}/comentarios', headers=self.cab(admin),
                                json=exemplo('/demandas/{id}/comentarios', 'post'))
        self.assertEqual(resp.status_code, 201)

    def test_respostas_reais_tem_os_campos_documentados(self):
        admin = self.login({'email': 'juliana@gmail.com', 'senha': 'juliana'})
        demanda = self.client.get('/api/v1/demandas/1', headers=self.cab(admin)).get_json()['dados']
        self.assertEqual(set(demanda), set(self.spec['components']['schemas']['Demanda']['properties']))
        dash = self.client.get('/api/v1/dashboard', headers=self.cab(admin)).get_json()['dados']
        self.assertEqual(set(dash), set(self.spec['components']['schemas']['DashboardResposta']
                                         ['properties']['dados']['properties']))
        log = self.client.get('/api/v1/logs?por_pagina=1', headers=self.cab(admin)).get_json()['dados'][0]
        self.assertEqual(set(log), set(self.spec['components']['schemas']['Log']['properties']))
        comentario = self.client.get('/api/v1/demandas/1/comentarios', headers=self.cab(admin)).get_json()['dados'][0]
        self.assertEqual(set(comentario), set(self.spec['components']['schemas']['Comentario']['properties']))


if __name__ == '__main__':
    unittest.main()