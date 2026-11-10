"""
Testes da API (fases 1 e 2: fundação + autenticação).
Rodar com:  python -m unittest test_api -v

Cada classe cria um banco temporário (via init_db.py) e aponta SGDI_DB para ele,
então o demandas.db real nunca é tocado.
"""
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

from flask import Flask, jsonify

PASTA = os.path.dirname(os.path.abspath(__file__))
if PASTA not in sys.path:
    sys.path.insert(0, PASTA)

from app import app as sgdi_app          # noqa: E402
from api import requer_token_api         # noqa: E402
from core import hash_token              # noqa: E402

ADMIN = {'email': 'juliana@gmail.com', 'senha': 'juliana'}
USUARIO = {'email': 'joaosilva@gmail.com', 'senha': 'joao'}
INTEGRACAO = {'email': 'integracao@sgdi.local', 'senha': 'integracao'}


class BaseApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pasta_tmp = tempfile.mkdtemp()
        cls.banco = os.path.join(cls.pasta_tmp, 'teste.db')
        os.environ['SGDI_DB'] = cls.banco
        subprocess.run([sys.executable, os.path.join(PASTA, 'init_db.py')],
                       cwd=PASTA, env=dict(os.environ), check=True, capture_output=True)
        cls.client = sgdi_app.test_client()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.pasta_tmp, ignore_errors=True)
        os.environ.pop('SGDI_DB', None)

    # -- auxiliares
    def login(self, credenciais):
        resp = self.client.post('/api/v1/auth/token', json=credenciais)
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        return resp.get_json()['dados']['token']

    @staticmethod
    def cab(token):
        return {'Authorization': f'Bearer {token}'}

    def sql(self, consulta, params=()):
        conn = sqlite3.connect(self.banco)
        conn.row_factory = sqlite3.Row
        try:
            linhas = conn.execute(consulta, params).fetchall()
            conn.commit()
            return linhas
        finally:
            conn.close()

    def assertErro(self, resp, status, codigo):
        self.assertEqual(resp.status_code, status)
        self.assertTrue(resp.is_json)
        corpo = resp.get_json()['erro']
        self.assertEqual(corpo['codigo'], codigo)
        self.assertIn('mensagem', corpo)
        self.assertIn('campos', corpo)


class TestAutenticacao(BaseApi):
    def test_sem_token_da_401_em_json(self):
        resp = self.client.get('/api/v1/me')
        self.assertErro(resp, 401, 'nao_autenticado')
        self.assertIn('Bearer', resp.headers['WWW-Authenticate'])

    def test_cabecalho_mal_formado(self):
        for valor in ('Basic abc', 'Bearer', 'token-solto'):
            resp = self.client.get('/api/v1/me', headers={'Authorization': valor})
            self.assertErro(resp, 401, 'nao_autenticado')

    def test_token_desconhecido(self):
        resp = self.client.get('/api/v1/me', headers=self.cab('nao-existe'))
        self.assertErro(resp, 401, 'token_invalido')

    def test_login_ok_e_resposta_sem_vazar_senha(self):
        resp = self.client.post('/api/v1/auth/token', json=ADMIN)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers['Cache-Control'], 'no-store')
        dados = resp.get_json()['dados']
        self.assertEqual(dados['tipo'], 'Bearer')
        self.assertTrue(dados['token'])
        self.assertEqual(dados['usuario']['perfil'], 'admin')
        self.assertNotIn('senha', dados['usuario'])
        self.assertNotIn('scrypt', resp.get_data(as_text=True))
        self.assertTrue(self.sql("SELECT 1 FROM logs WHERE acao='API_LOGIN' AND detalhes LIKE '%origem=api%'"))

    def test_token_guardado_apenas_como_hash(self):
        token = self.login(ADMIN)
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM api_tokens WHERE token_hash = ?", (token,))[0]['n'], 0)
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM api_tokens WHERE token_hash = ?",
                                  (hash_token(token),))[0]['n'], 1)

    def test_login_email_case_insensitive(self):
        resp = self.client.post('/api/v1/auth/token',
                                json={'email': '  JULIANA@gmail.com ', 'senha': 'juliana'})
        self.assertEqual(resp.status_code, 200)

    def test_login_senha_errada(self):
        resp = self.client.post('/api/v1/auth/token', json={'email': ADMIN['email'], 'senha': 'errada'})
        self.assertErro(resp, 401, 'credenciais_invalidas')
        resp = self.client.post('/api/v1/auth/token', json={'email': 'ninguem@x.com', 'senha': 'x'})
        self.assertErro(resp, 401, 'credenciais_invalidas')   # mesma resposta: não revela e-mails
        self.assertTrue(self.sql("SELECT 1 FROM logs WHERE acao='API_LOGIN_FALHA'"))

    def test_login_validacao(self):
        resp = self.client.post('/api/v1/auth/token', json={'email': ''})
        self.assertErro(resp, 422, 'validacao')
        self.assertEqual(set(resp.get_json()['erro']['campos']), {'email', 'senha'})
        resp = self.client.post('/api/v1/auth/token', json={'email': 123, 'senha': 'x'})
        self.assertErro(resp, 422, 'validacao')

    def test_login_corpo_nao_json(self):
        resp = self.client.post('/api/v1/auth/token', data='email=a&senha=b')
        self.assertErro(resp, 400, 'requisicao_invalida')
        resp = self.client.post('/api/v1/auth/token', data='{quebrado', content_type='application/json')
        self.assertErro(resp, 400, 'requisicao_invalida')
        resp = self.client.post('/api/v1/auth/token', json=['lista'])
        self.assertErro(resp, 400, 'requisicao_invalida')

    def test_me(self):
        resp = self.client.get('/api/v1/me', headers=self.cab(self.login(USUARIO)))
        self.assertEqual(resp.status_code, 200)
        dados = resp.get_json()['dados']
        self.assertEqual(dados['usuario']['email'], USUARIO['email'])
        self.assertEqual(dados['permissoes'], ['comentar', 'criar_demanda', 'visualizar'])
        self.assertIn('expira_em', dados['token'])

    def test_me_integracao_tem_permissoes_minimas(self):
        dados = self.client.get('/api/v1/me', headers=self.cab(self.login(INTEGRACAO))).get_json()['dados']
        self.assertEqual(dados['usuario']['perfil'], 'integracao')
        self.assertNotIn('ver_logs', dados['permissoes'])
        self.assertNotIn('excluir_demanda', dados['permissoes'])

    def test_token_expirado(self):
        token = self.login(ADMIN)
        passado = (datetime.now() - timedelta(minutes=1)).strftime('%Y-%m-%d %H:%M:%S')
        self.sql("UPDATE api_tokens SET expira_em = ? WHERE token_hash = ?", (passado, hash_token(token)))
        self.assertErro(self.client.get('/api/v1/me', headers=self.cab(token)), 401, 'token_expirado')

    def test_revogar_token(self):
        token = self.login(ADMIN)
        outro = self.login(ADMIN)
        resp = self.client.delete('/api/v1/auth/token', headers=self.cab(token))
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(resp.get_data(), b'')
        self.assertErro(self.client.get('/api/v1/me', headers=self.cab(token)), 401, 'token_invalido')
        # revogar um token não derruba os outros do mesmo usuário
        self.assertEqual(self.client.get('/api/v1/me', headers=self.cab(outro)).status_code, 200)
        self.assertTrue(self.sql("SELECT 1 FROM logs WHERE acao='API_LOGOUT'"))

    def test_perfil_lido_do_banco_a_cada_requisicao(self):
        token = self.login(USUARIO)
        self.sql("UPDATE solicitantes SET perfil='admin' WHERE email=?", (USUARIO['email'],))
        try:
            permissoes = self.client.get('/api/v1/me', headers=self.cab(token)).get_json()['dados']['permissoes']
            self.assertIn('ver_logs', permissoes)
        finally:
            self.sql("UPDATE solicitantes SET perfil='usuario' WHERE email=?", (USUARIO['email'],))

    def test_cookie_de_sessao_nao_autentica_a_api(self):
        cliente = sgdi_app.test_client()
        resp = cliente.post('/login', data={'email': ADMIN['email'], 'senha': ADMIN['senha']})
        self.assertEqual(resp.status_code, 302)             # login web funcionou
        self.assertEqual(cliente.get('/').status_code, 200)  # sessão válida nas telas
        self.assertErro(cliente.get('/api/v1/me'), 401, 'nao_autenticado')

    def test_erros_html_viram_json_so_na_api(self):
        self.assertErro(self.client.get('/api/v1/nao-existe'), 404, 'nao_encontrado')
        self.assertErro(self.client.get('/api/v2/me'), 404, 'nao_encontrado')
        resp = self.client.get('/api/v1/auth/token')
        self.assertErro(resp, 405, 'metodo_nao_permitido')
        self.assertIn('POST', resp.headers['Allow'])
        # telas continuam como antes: sem sessão, redireciona para /login
        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login', resp.headers['Location'])

    def test_corpo_gigante_da_413_em_json(self):
        resp = self.client.post('/api/v1/auth/token', data='x' * (1024 * 1024 + 10),
                                content_type='application/json')
        self.assertErro(resp, 413, 'corpo_grande_demais')


class TestPermissaoDoDecorator(BaseApi):
    """Ainda não há endpoint com permissão nas fases 1-2; testamos o decorator numa mini-app."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        mini = Flask('mini')

        @mini.route('/protegido')
        @requer_token_api('ver_logs')
        def protegido():
            return jsonify(ok=True)

        cls.mini = mini.test_client()

    def test_403_sem_permissao_e_log(self):
        token = self.login(USUARIO)
        resp = self.mini.get('/protegido', headers=self.cab(token))
        self.assertErro(resp, 403, 'acesso_negado')
        self.assertTrue(self.sql("SELECT 1 FROM logs WHERE acao='ACESSO_NEGADO' AND detalhes LIKE '%permissao=ver_logs%'"))

    def test_200_com_permissao(self):
        resp = self.mini.get('/protegido', headers=self.cab(self.login(ADMIN)))
        self.assertEqual(resp.status_code, 200)

    def test_integracao_nao_le_logs(self):
        resp = self.mini.get('/protegido', headers=self.cab(self.login(INTEGRACAO)))
        self.assertErro(resp, 403, 'acesso_negado')


class TestForcaBruta(BaseApi):
    def test_bloqueia_apos_5_falhas(self):
        for _ in range(5):
            resp = self.client.post('/api/v1/auth/token', json={'email': ADMIN['email'], 'senha': 'errada'})
            self.assertEqual(resp.status_code, 401)
        # a 6ª tentativa é barrada, mesmo com a senha certa
        resp = self.client.post('/api/v1/auth/token', json=ADMIN)
        self.assertErro(resp, 429, 'muitas_tentativas')
        espera = int(resp.headers['Retry-After'])
        self.assertTrue(1 <= espera <= 15 * 60)
        # tentativas barradas não entram na contagem (não prolongam o bloqueio)
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM logs WHERE acao='API_LOGIN_FALHA'")[0]['n'], 5)


if __name__ == '__main__':
    unittest.main()