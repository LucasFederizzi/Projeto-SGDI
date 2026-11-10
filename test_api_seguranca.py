"""
Checklist de segurança da API (fase 6), em forma de teste automatizado.
Rodar com:  python -m unittest test_api_seguranca -v

O que é verificado:
  1. toda rota da API exige token (menos a emissão do token);
  2. cada rota exige a permissão certa, para cada perfil (matriz gerada a partir do código);
  3. nenhuma resposta vaza hash de senha e o token nunca aparece nos logs nem no banco em texto puro;
  4. injeção de SQL em filtros, ordenação, corpo e login;
  5. token só vale pelo cabeçalho Authorization (não por URL, corpo ou cookie);
  6. cabeçalhos de segurança, ausência de CORS, tokens fortes e únicos;
  7. faxina de tokens vencidos.
"""
import re
import sqlite3
from datetime import datetime, timedelta
from unittest import mock

from core import PERMISSOES_POR_PERFIL, hash_token
from test_api import ADMIN, INTEGRACAO, USUARIO, BaseApi, sgdi_app

ID_INEXISTENTE = 999999   # rotas "permitidas" respondem 404/400 e nunca alteram dados reais


def rotas_da_api():
    """(método, caminho de exemplo, função da view) de cada rota /api/v1."""
    for regra in sgdi_app.url_map.iter_rules():
        if not regra.endpoint.startswith('api.'):
            continue
        caminho = re.sub(r'<[^>]*:id>', str(ID_INEXISTENTE), regra.rule)
        for metodo in sorted(regra.methods - {'HEAD', 'OPTIONS'}):
            yield metodo, caminho, regra.endpoint, sgdi_app.view_functions[regra.endpoint]


class TestAutorizacao(BaseApi):
    def test_toda_rota_exige_token(self):
        verificadas = 0
        for metodo, caminho, endpoint, _ in rotas_da_api():
            if endpoint == 'api.emitir_token':          # única rota pública (é o login)
                continue
            resp = self.client.open(caminho, method=metodo)
            self.assertErro(resp, 401, 'nao_autenticado')
            verificadas += 1
        self.assertEqual(verificadas, 14)

    def test_toda_rota_declara_uma_permissao_conscientemente(self):
        """Rota nova sem @requer_token_api, ou sem permissão, derruba este teste de propósito."""
        sem_permissao = set()
        for metodo, caminho, endpoint, view in rotas_da_api():
            if endpoint == 'api.emitir_token':
                continue
            self.assertTrue(hasattr(view, 'permissao_exigida'), f'{metodo} {caminho} sem @requer_token_api')
            if view.permissao_exigida is None:
                sem_permissao.add(endpoint)
            else:
                self.assertIn(view.permissao_exigida,
                              set().union(*PERMISSOES_POR_PERFIL.values()), endpoint)
        # só quem apenas precisa de um token válido (qualquer perfil) pode ficar sem permissão
        self.assertEqual(sem_permissao, {'api.me', 'api.revogar_token'})

    def test_matriz_de_permissoes_por_perfil(self):
        tokens = {'usuario': self.login(USUARIO), 'integracao': self.login(INTEGRACAO), 'admin': self.login(ADMIN)}
        checagens = 0
        for metodo, caminho, endpoint, view in rotas_da_api():
            permissao = getattr(view, 'permissao_exigida', None)
            if endpoint == 'api.emitir_token' or endpoint == 'api.revogar_token':
                continue   # revogar apagaria o token de teste; coberto em test_api.py
            for perfil, token in tokens.items():
                resp = self.client.open(caminho, method=metodo, headers=self.cab(token), json={})
                codigo = resp.get_json()['erro']['codigo'] if resp.is_json and resp.status_code >= 400 else None
                deve_negar = permissao is not None and permissao not in PERMISSOES_POR_PERFIL[perfil]
                if deve_negar:
                    self.assertEqual((resp.status_code, codigo), (403, 'acesso_negado'),
                                     f'{perfil} não deveria acessar {metodo} {caminho}')
                else:
                    self.assertNotEqual(codigo, 'acesso_negado', f'{perfil} deveria acessar {metodo} {caminho}')
                    self.assertLess(resp.status_code, 500, f'{perfil} {metodo} {caminho}')
                checagens += 1
        self.assertEqual(checagens, 13 * 3)


class TestVazamentos(BaseApi):
    def test_respostas_nunca_trazem_hash_de_senha(self):
        admin = self.login(ADMIN)
        for url in ('/me', '/demandas', '/demandas/1', '/demandas/1/comentarios', '/dashboard', '/logs?por_pagina=100'):
            texto = self.client.get('/api/v1' + url, headers=self.cab(admin)).get_data(as_text=True)
            for marcador in ('scrypt:', 'pbkdf2:', '"senha"'):
                self.assertNotIn(marcador, texto, f'{marcador} vazou em {url}')
        login = self.client.post('/api/v1/auth/token', json=ADMIN).get_data(as_text=True)
        self.assertNotIn('scrypt:', login)
        self.assertNotIn('"senha"', login)

    def test_token_nunca_aparece_nos_logs_nem_no_banco_em_texto_puro(self):
        token = self.login(ADMIN)
        self.client.get('/api/v1/me', headers=self.cab(token))
        self.client.get('/api/v1/demandas/abc', headers=self.cab(token))
        conn = sqlite3.connect(self.banco)
        try:
            for tabela, coluna in (('logs', 'detalhes'), ('logs', 'usuario_nome'), ('api_tokens', 'token_hash')):
                achou = conn.execute(f'SELECT COUNT(*) FROM {tabela} WHERE {coluna} LIKE ?', (f'%{token}%',)).fetchone()[0]
                self.assertEqual(achou, 0, f'token em texto puro em {tabela}.{coluna}')
        finally:
            conn.close()

    def test_token_forte_e_unico(self):
        tokens = {self.login(ADMIN) for _ in range(5)}
        self.assertEqual(len(tokens), 5)
        self.assertTrue(all(len(t) >= 40 for t in tokens))


class TestInjecao(BaseApi):
    CARGAS = ["' OR '1'='1", "1; DROP TABLE demandas; --", '" OR ""="', "%' OR 1=1 --", "\\"]

    def contar(self, tabela):
        return self.sql(f'SELECT COUNT(*) AS n FROM {tabela}')[0]['n']

    def test_filtros_da_listagem(self):
        admin = self.login(ADMIN)
        antes = self.contar('demandas')
        for carga in self.CARGAS:
            resp = self.client.get('/api/v1/demandas', headers=self.cab(admin), query_string={'busca': carga})
            self.assertEqual(resp.status_code, 200, carga)
            self.assertEqual(resp.get_json()['paginacao']['total'], 0, f'busca {carga!r} devolveu linhas')
            for parametro in ('status', 'prioridade', 'ordenar', 'responsavel_id', 'solicitante_id', 'criada_de'):
                resp = self.client.get('/api/v1/demandas', headers=self.cab(admin), query_string={parametro: carga})
                self.assertIn(resp.status_code, (200, 422), f'{parametro}={carga!r}')
                self.assertNotEqual(resp.status_code, 500)
            resp = self.client.get('/api/v1/logs', headers=self.cab(admin), query_string={'acao': carga})
            self.assertEqual(resp.status_code, 422)
        self.assertEqual(self.contar('demandas'), antes)

    def test_ordenacao_so_aceita_lista_fixa(self):
        admin = self.login(ADMIN)
        resp = self.client.get('/api/v1/demandas?ordenar=prazo;DROP TABLE demandas', headers=self.cab(admin))
        self.assertErro(resp, 422, 'validacao')

    def test_corpo_com_sql_e_gravado_como_texto_inerte(self):
        admin = self.login(ADMIN)
        antes = self.contar('demandas')
        carga = "x'); DROP TABLE demandas; --"
        corpo = {'titulo': 'Titulo normal', 'descricao': carga, 'prioridade': 'Alta', 'prazo': '2030-01-01'}
        resp = self.client.post('/api/v1/demandas', headers=self.cab(admin), json=corpo)
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.get_json()['dados']['descricao'], carga)
        with mock.patch('api.TITULO_SEM_PONTUACAO_API', False):      # título com pontuação liberado
            resp = self.client.post('/api/v1/demandas', headers=self.cab(admin), json={**corpo, 'titulo': carga})
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.get_json()['dados']['titulo'], carga)
        resp = self.client.post(f"/api/v1/demandas/{resp.get_json()['dados']['id']}/comentarios",
                                headers=self.cab(admin), json={'comentario': carga})
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(self.contar('demandas'), antes + 2)         # a tabela continua lá

    def test_login_com_injecao(self):
        for carga in self.CARGAS[:3]:
            resp = self.client.post('/api/v1/auth/token', json={'email': carga, 'senha': carga})
            self.assertErro(resp, 401, 'credenciais_invalidas')


class TestApenasCabecalhoAuthorization(BaseApi):
    def test_token_na_url_no_corpo_ou_em_cookie_nao_vale(self):
        token = self.login(ADMIN)
        for tentativa in (
            self.client.get(f'/api/v1/me?token={token}'),
            self.client.get(f'/api/v1/me?access_token={token}'),
            self.client.get('/api/v1/me', json={'token': token}),
            self.client.get('/api/v1/me', headers={'Cookie': f'token={token}'}),
            self.client.get('/api/v1/me', headers={'X-Api-Key': token}),
        ):
            self.assertErro(tentativa, 401, 'nao_autenticado')

    def test_cabecalho_com_esquema_bearer_em_maiusculas_ou_minusculas(self):
        token = self.login(ADMIN)
        for esquema in ('Bearer', 'bearer', 'BEARER'):
            resp = self.client.get('/api/v1/me', headers={'Authorization': f'{esquema} {token}'})
            self.assertEqual(resp.status_code, 200, esquema)


class TestCabecalhosECors(BaseApi):
    def test_cabecalhos_em_toda_resposta_da_api(self):
        token = self.login(ADMIN)
        respostas = [
            self.client.get('/api/v1/me', headers=self.cab(token)),          # 200
            self.client.get('/api/v1/me'),                                    # 401
            self.client.get('/api/v1/nao-existe'),                            # 404 do Flask
            self.client.get('/api/v1/auth/token'),                            # 405 do Flask
            self.client.post('/api/v1/auth/token', json=ADMIN),               # token emitido
            self.client.delete('/api/v1/auth/token', headers=self.cab(token)),   # 204
        ]
        for resp in respostas:
            self.assertEqual(resp.headers.get('Cache-Control'), 'no-store', resp.status_code)
            self.assertEqual(resp.headers.get('X-Content-Type-Options'), 'nosniff', resp.status_code)

    def test_sem_cors_aberto(self):
        token = self.login(ADMIN)
        origem = {'Origin': 'https://site-malicioso.example'}
        for resp in (self.client.get('/api/v1/me', headers={**self.cab(token), **origem}),
                     self.client.options('/api/v1/demandas', headers={**origem, 'Access-Control-Request-Method': 'GET'})):
            self.assertNotIn('Access-Control-Allow-Origin', resp.headers)

    def test_modo_debug_desligado_nos_testes(self):
        self.assertFalse(sgdi_app.debug)    # o debugger do Werkzeug permite executar código: nunca em produção


class TestFaxinaDeTokens(BaseApi):
    def test_login_apaga_tokens_vencidos_ha_mais_de_7_dias(self):
        velho = (datetime.now() - timedelta(days=8)).strftime('%Y-%m-%d %H:%M:%S')
        recente = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
        self.sql("INSERT INTO api_tokens (token_hash, usuario_id, criado_em, expira_em) VALUES ('velho', 1, ?, ?)", (velho, velho))
        self.sql("INSERT INTO api_tokens (token_hash, usuario_id, criado_em, expira_em) VALUES ('recente', 1, ?, ?)", (recente, recente))
        self.login(ADMIN)
        hashes = {l['token_hash'] for l in self.sql("SELECT token_hash FROM api_tokens")}
        self.assertNotIn('velho', hashes)
        self.assertIn('recente', hashes)

    def test_token_de_usuario_removido_deixa_de_valer(self):
        token = self.login(USUARIO)
        original = tuple(self.sql("SELECT id, nome, email, senha, perfil FROM solicitantes WHERE email = ?",
                                  (USUARIO['email'],))[0])
        self.sql("DELETE FROM solicitantes WHERE email = ?", (USUARIO['email'],))
        try:
            self.assertErro(self.client.get('/api/v1/me', headers=self.cab(token)), 401, 'token_invalido')
        finally:
            self.sql("INSERT INTO solicitantes (id, nome, email, senha, perfil) VALUES (?, ?, ?, ?, ?)", original)