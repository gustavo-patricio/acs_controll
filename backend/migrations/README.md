# Migrations do banco

Execute `uv run alembic upgrade head` dentro de `backend/`, usando
`ACS_DATABASE_URL` do ambiente ou do `.env` local. `uv run alembic check` verifica
se os modelos e o banco migrado concordam.

- `0001_cwmp_inform`: CPEs, sessões e eventos.
- `0002_auth_accounts`: contas de autenticação.
- `0003_shared_cpe_credentials`: contas compartilhadas explícitas.
- `0004_inventory_provenance`: origem, perfil e horários por campo do inventário.
  Preserva valores legados sem inferir seus metadados. Consulte
  [as regras de rastreabilidade](../docs/INVENTORY_PROVENANCE.md).
- `0005_read_queries`: operações e tentativas de leitura, sessão aguardando resposta
  e origem Inform/GPV nos metadados. Preserva as observações existentes como Inform.
  O downgrade recusa execução quando há histórico de operações; requer estratégia
  explícita de arquivamento antes de remover esses dados.

Downgrades podem remover dados. O downgrade de `0004` descarta a tabela de
metadados, mas preserva os valores das CPEs e o histórico de sessões/eventos.
Os testes de integração exercitam upgrades e downgrades em schemas descartáveis.
