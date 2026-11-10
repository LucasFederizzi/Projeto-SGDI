# SGDI - Sistema de Gestão de Demandas Internas

Sistema para gerenciar demandas internas da empresa: telas web (Flask + SQLite), dashboard
gerencial, **logs de auditoria** e uma **API REST segura e documentada** para integração com
sistemas externos.

## Como rodar

```bash
pip install -r requirements.txt
python init_db.py      # recria usuários, demandas e comentários de TESTE (os logs são preservados)
python app.py
```

- Telas: http://localhost:5000
- Documentação da API (Swagger UI): http://localhost:5000/api/docs
- Especificação OpenAPI: http://localhost:5000/api/openapi.yaml

> O `init_db.py` apaga e recria `solicitantes`, `demandas`, `comentarios` e `api_tokens`
> (tokens antigos ficariam órfãos). A tabela `logs` nunca é apagada.

### Variáveis de ambiente (todas opcionais)

| Variável | Para que serve | Padrão |
|---|---|---|
| `SECRET_KEY` | Chave das sessões das telas. **Obrigatória em produção** | chave de desenvolvimento |
| `FLASK_DEBUG=1` | Modo debug. **Nunca em produção nem em rede aberta** (o debugger permite executar código) | desligado |
| `SGDI_HOST` | Endereço de escuta (`0.0.0.0` expõe na rede) | `127.0.0.1` |
| `SGDI_DB` | Caminho do arquivo SQLite | `demandas.db` |
| `SGDI_API_TOKEN_HORAS` | Validade do token da API, em horas | `12` |
| `SGDI_API_TITULO_SEM_PONTUACAO` | `0` libera pontuação no título **só na API** | `1` (proíbe, como nas telas) |
| `NOME_EMPRESA` | Nome que aparece no relatório impresso | SGDI |

## Funcionalidades das telas

- Criar, editar e excluir demandas, com busca, ordenação e paginação
- Detalhes da demanda: assumir responsabilidade, concluir, cancelar e comentar
- Dashboard gerencial com filtros, gráficos, exportação CSV e relatório para impressão/PDF
- Logs de auditoria (`/logs`)

## Perfis e permissões

O perfil fica na coluna `solicitantes.perfil` e vale tanto para as telas quanto para a API.
Para criar um perfil novo, edite `PERMISSOES_POR_PERFIL` e `ROTULOS_PERFIL` em `core.py`.

| Permissão | usuario | integracao | admin |
|---|:-:|:-:|:-:|
| visualizar | sim | sim | sim |
| criar_demanda | sim | sim | sim |
| comentar | sim | sim | sim |
| alterar_status (assumir, concluir, cancelar) | não | sim | sim |
| editar_demanda | não | não | sim |
| excluir_demanda | não | não | sim |
| ver_painel (dashboard) | não | não | sim |
| ver_logs | não | não | sim |

Regras de negócio (valem nas telas e na API): só o responsável conclui a demanda; demanda
concluída ou cancelada não muda de status; cada demanda tem no máximo um responsável.

Contas de teste (e-mail / senha): `juliana@gmail.com` / `juliana`, `lucas@gmail.com` / `lucas`,
`techio@gmail.com` / `techio` (admins); `joaosilva@gmail.com` / `joao`, `mariasantos@gmail.com` / `maria`,
`pedrocosta@gmail.com` / `pedro`, `analima@gmail.com` / `ana` (usuários);
`integracao@sgdi.local` / `integracao` (conta de serviço para a API).
**Troque essas senhas fora do ambiente de testes.**

## Logs de auditoria

Tabela `logs` (somente inserção): data/hora, usuário, ação, entidade, ID, detalhes e IP.
Ações registradas: `LOGIN`, `LOGIN_FALHA`, `LOGOUT`, `CRIAR_DEMANDA`, `EDITAR_DEMANDA` (antes -> depois),
`EXCLUIR_DEMANDA`, `ASSUMIR_RESPONSABILIDADE`, `CONCLUIR_DEMANDA`, `CANCELAR_DEMANDA`, `CRIAR_COMENTARIO`,
`ACESSO_NEGADO`, `INICIALIZAR_BANCO` e, da API, `API_LOGIN`, `API_LOGIN_FALHA` e `API_LOGOUT`.
Cada log é gravado na mesma transação da operação. Ações feitas pela API terminam com `origem=api` nos detalhes.

---

## API REST (v1)

Base: `http://localhost:5000/api/v1`. Documentação interativa em **`/api/docs`** (botão **Authorize**
para colar o token e testar). A referência completa de rotas, parâmetros, exemplos e códigos de erro
está em `openapi.yaml`.

### Padrões

- JSON em tudo (`Content-Type: application/json` nas requisições com corpo).
- **Sucesso:** `{"dados": ...}`; listas trazem também `"paginacao": {pagina, por_pagina, total, total_paginas}`.
- **Erro:** `{"erro": {"codigo": "...", "mensagem": "...", "campos": {...}}}`.
- **Códigos HTTP:** 200, 201 (criado, com `Location`), 204 (sem conteúdo), 400, 401, 403, 404,
  405, 409 (regra de negócio), 413, 422 (validação), 429 (muitas tentativas) e 500.
- Cada demanda traz `status` (gravado) e `status_exibido` (inclui `Atrasado` quando está aberta com o prazo vencido).

### Autenticação

1. `POST /auth/token` com e-mail e senha devolve um **token Bearer** (válido por 12h por padrão).
   O token só aparece nessa resposta; no banco fica apenas o hash.
2. Envie `Authorization: Bearer <token>` nas demais chamadas. Cookies, parâmetros de URL e corpo **não** autenticam.
3. `DELETE /auth/token` revoga o token.

Depois de 5 logins falhos do mesmo IP em 15 minutos, a API responde 429 com `Retry-After`.

### Rotas

| Método | Rota | Permissão |
|---|---|---|
| POST | `/auth/token` | pública (é o login) |
| DELETE | `/auth/token` | token válido |
| GET | `/me` | token válido |
| GET | `/demandas` | visualizar |
| POST | `/demandas` | criar_demanda |
| GET | `/demandas/{id}` | visualizar |
| PATCH | `/demandas/{id}` | editar_demanda |
| DELETE | `/demandas/{id}` | excluir_demanda |
| POST | `/demandas/{id}/responsavel` | alterar_status |
| POST | `/demandas/{id}/concluir` | alterar_status |
| POST | `/demandas/{id}/cancelar` | alterar_status |
| GET | `/demandas/{id}/comentarios` | visualizar |
| POST | `/demandas/{id}/comentarios` | comentar |
| GET | `/dashboard` | ver_painel |
| GET | `/logs` | ver_logs |

Filtros de `GET /demandas`: `status`, `prioridade`, `responsavel_id` (id ou `sem`), `solicitante_id`,
`busca`, `criada_de`, `criada_ate` (AAAA-MM-DD), `ordenar` (`solicitante`, `data`, `prioridade`, `prazo`),
`pagina` e `por_pagina` (máx. 100). Filtro inválido devolve 422.

### Exemplo completo com curl (login, criar, assumir, concluir, sair)

```bash
BASE=http://localhost:5000/api/v1

# 1) login: guarda o token em $TOKEN
TOKEN=$(curl -s -X POST $BASE/auth/token -H "Content-Type: application/json" \
  -d '{"email":"integracao@sgdi.local","senha":"integracao"}' \
  | python -c "import sys,json; print(json.load(sys.stdin)['dados']['token'])")

# 2) quem sou eu
curl -s $BASE/me -H "Authorization: Bearer $TOKEN"

# 3) criar uma demanda (anote o "id" da resposta; o solicitante é o dono do token)
curl -s -X POST $BASE/demandas -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"titulo":"Integrar com o ERP","descricao":"Enviar demandas ao ERP","prioridade":"Alta","prazo":"2030-12-31"}'

# 4) assumir e concluir (troque 16 pelo id criado)
curl -s -X POST $BASE/demandas/16/responsavel -H "Authorization: Bearer $TOKEN"
curl -s -X POST $BASE/demandas/16/concluir    -H "Authorization: Bearer $TOKEN"

# 5) listar com filtros (acentos precisam ir codificados na URL no curl)
curl -s -G $BASE/demandas -H "Authorization: Bearer $TOKEN" \
  --data-urlencode "status=Concluído" --data-urlencode "por_pagina=5"

# 6) sair (revoga o token)
curl -s -X DELETE $BASE/auth/token -H "Authorization: Bearer $TOKEN"
```

No Windows (PowerShell), o equivalente é `Invoke-RestMethod`:

```powershell
$base  = "http://localhost:5000/api/v1"
$login = Invoke-RestMethod -Method Post -Uri "$base/auth/token" -ContentType "application/json" `
         -Body '{"email":"integracao@sgdi.local","senha":"integracao"}'
$h     = @{ Authorization = "Bearer $($login.dados.token)" }
$nova  = Invoke-RestMethod -Method Post -Uri "$base/demandas" -Headers $h -ContentType "application/json" `
         -Body '{"titulo":"Integrar com o ERP","descricao":"Enviar demandas ao ERP","prioridade":"Alta","prazo":"2030-12-31"}'
Invoke-RestMethod -Method Post -Uri "$base/demandas/$($nova.dados.id)/responsavel" -Headers $h
Invoke-RestMethod -Method Post -Uri "$base/demandas/$($nova.dados.id)/concluir"    -Headers $h
Invoke-RestMethod -Method Delete -Uri "$base/auth/token" -Headers $h
```

---

## Organização do código

| Arquivo | Papel |
|---|---|
| `app.py` | Telas HTML, login/logout, dashboard e relatórios; registra os blueprints |
| `api.py` | API REST (`/api/v1`): autenticação por token, rotas, serialização e erros em JSON |
| `servicos.py` | Regras de negócio compartilhadas (validação, status, comentários, logs) |
| `core.py` | Constantes, perfis/permissões, banco, auditoria e cálculos do dashboard |
| `docs.py` | Swagger UI (`/api/docs`) e entrega do `openapi.yaml` |
| `openapi.yaml` | Especificação OpenAPI 3 da API |
| `init_db.py` | Recria o banco de teste |
| `templates/`, `static/` | Páginas HTML e CSS |

Regra de ouro: `core.py`, `servicos.py`, `api.py` e `docs.py` **nunca importam `app.py`** (ele roda como
`__main__`; importá-lo criaria uma segunda aplicação Flask). Código compartilhado vai para `core.py`/`servicos.py`.

Ao criar uma rota nova na API: proteja com `@requer_token_api('permissao')`, coloque a regra de negócio
em `servicos.py` e documente no `openapi.yaml`. Os testes avisam se faltar qualquer um desses passos.

## Testes

```bash
python -m unittest discover -p "test_*.py" -v
```

| Arquivo | O que cobre |
|---|---|
| `test_api.py` | Autenticação: token, expiração, revogação, cookie ignorado, força bruta, erros em JSON |
| `test_api_crud.py` | Leitura e escrita de demandas/comentários, regras de status, concorrência e as telas HTML |
| `test_api_docs.py` | Documentação em dia com o código: rotas, códigos de erro, permissões, exemplos |
| `test_api_seguranca.py` | Checklist de segurança: autorização por rota e perfil, vazamentos, injeção de SQL, cabeçalhos |

Os testes usam um banco temporário (via `SGDI_DB`) e não tocam o `demandas.db`. Parte de `test_api_docs.py`
usa PyYAML e é pulada se ele não estiver instalado (`pip install pyyaml`).

## Segurança: o que está implementado e o que falta

Implementado e testado:

- Token opaco e aleatório; só o **hash SHA-256** fica no banco; expira e pode ser revogado.
- Perfil e permissões lidos do banco a cada requisição (mudou o perfil, vale na hora).
- Cookie de sessão ignorado pela API (sem risco de CSRF contra ela).
- Todas as rotas exigem token e permissão; os testes percorrem a lista de rotas para garantir isso.
- Limite de tentativas de login por IP; mesma resposta para e-mail inexistente e senha errada.
- Consultas SQL parametrizadas; ordenação e colunas editáveis vêm de listas fixas.
- Respostas com campos explícitos (nunca o hash da senha); campos desconhecidos no corpo são rejeitados.
- `Cache-Control: no-store` e `X-Content-Type-Options: nosniff` em tudo que está sob `/api`; sem CORS aberto.
- Corpo de requisição limitado a 1 MB; toda ação da API fica no log com `origem=api`.

Para produção, ainda é preciso:

- **HTTPS** (o token trafega em cabeçalho) e uma `SECRET_KEY` forte por variável de ambiente.
- Servidor de produção (por exemplo Gunicorn/Waitress) e **nunca** `FLASK_DEBUG=1`.
- Atrás de proxy reverso, configurar o `ProxyFix` do Werkzeug; senão o limite de login enxerga só o IP do proxy.
- Trocar as senhas das contas de teste (inclusive a de `integracao`).
- As **telas** não têm proteção CSRF (limitação anterior à API; a API não é afetada). Se o sistema for
  exposto de verdade, vale adotar o Flask-WTF/CSRFProtect.
- Se um front-end de outro domínio for consumir a API pelo navegador, será preciso configurar CORS (integração
  servidor a servidor não precisa).