"""
Documentação da API: Swagger UI em /api/docs e a especificação em /api/openapi.yaml.

As duas rotas são públicas de propósito (quem vai integrar precisa ler a documentação
antes de ter um token) e não expõem dado nenhum do sistema. O Swagger UI é carregado
de uma CDN, como o Chart.js do dashboard (precisa de internet no navegador).

Este módulo não importa app.py nem api.py (ver explicação no core.py).
"""
import os

from flask import Blueprint, Response, url_for

docs_bp = Blueprint('docs', __name__, url_prefix='/api')

CAMINHO_OPENAPI = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'openapi.yaml')
VERSAO_SWAGGER_UI = '5.17.14'   # versão fixa: um update da CDN não muda a página sem querer

_PAGINA = '''<!DOCTYPE html>
<html lang="pt-br">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>SGDI API - Documentação</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@__VERSAO__/swagger-ui.css">
</head>
<body>
    <div id="swagger-ui">
        <p style="font-family: Arial, sans-serif; padding: 20px;">
            Carregando a documentação... Se nada aparecer, o Swagger UI não carregou
            (verifique a internet). A especificação crua está em
            <a href="__YAML__">__YAML__</a>.
        </p>
    </div>
    <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@__VERSAO__/swagger-ui-bundle.js"></script>
    <script>
        window.onload = function () {
            if (typeof SwaggerUIBundle === 'undefined') return;
            window.ui = SwaggerUIBundle({
                url: '__YAML__',
                dom_id: '#swagger-ui',
                deepLinking: true,
                persistAuthorization: true
            });
        };
    </script>
</body>
</html>
'''


@docs_bp.route('/docs')
def swagger_ui():
    html = (_PAGINA.replace('__VERSAO__', VERSAO_SWAGGER_UI)
                   .replace('__YAML__', url_for('docs.especificacao')))
    return Response(html, mimetype='text/html')


@docs_bp.route('/openapi.yaml')
def especificacao():
    with open(CAMINHO_OPENAPI, encoding='utf-8') as arquivo:
        return Response(arquivo.read(), mimetype='application/yaml')