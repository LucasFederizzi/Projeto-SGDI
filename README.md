# SGDI - Sistema de Gestão de Demandas Internas

Sistema para gerenciar demandas internas da empresa, com **logs de auditoria** (Sprint 5).

## Como rodar

```bash
pip install -r requirements.txt
python init_db.py    
python app.py
```

Acesse: http://localhost:5000

Variáveis opcionais: `SECRET_KEY`, `FLASK_DEBUG=1`, `SGDI_HOST=0.0.0.0`.

## Funcionalidades

- Criar, editar e deletar demandas (somente o solicitante edita/deleta)
- Visualizar detalhes, assumir responsabilidade, concluir e cancelar
- Comentários
- Logs de auditoria (`/logs`, apenas perfil `auditor`)

## Logs de auditoria

Tabela `logs` (append-only): data/hora, usuário, ação, entidade, ID, detalhes e IP.
Ações registradas: LOGIN, LOGIN_FALHA, LOGOUT, CRIAR_DEMANDA, EDITAR_DEMANDA (antes -> depois),
EXCLUIR_DEMANDA, ASSUMIR_RESPONSABILIDADE, CONCLUIR_DEMANDA, CANCELAR_DEMANDA, CRIAR_COMENTARIO.
Cada log é gravado na mesma transação da operação.

Usuários de teste: juliana/lucas/techio são auditores (senha = primeiro nome, ex.: `juliana@gmail.com` / `juliana`).
