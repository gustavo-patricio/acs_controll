# Ponto de retomada — compatibilidade e telemetria

Atualizado em 27/10/2026 (UTC). Este documento fecha a revisão da base existente;
não declara concluída a homologação WAN/óptica nem a arquitetura futura do coletor.

## Entrega atual: pacote de evidências v1

Implementados `acs-evidence create` e `acs-evidence verify`: reconstrução anonimizada,
manifesto versionado com hash/tamanho e identificação técnica, horário declarado ou
explicitamente desconhecido, verificação offline e armazenamento privado. Consulte
[comandos e contrato](EVIDENCE_PACKAGE.md). Não houve novas dependências, migrations,
endpoints, consultas remotas ou reinicialização de serviços nesta entrega.

A entrada não é confiada como já anonimizada: sempre se reconstrói somente a lista
permitida. O pacote é parcial e não comprova autenticidade nem unidades/tipos
originais. A publicação usa manifesto final como marcador de conclusão; arquivos
incompletos e destinos existentes são rejeitados. Testes incluem CLI sem rede,
XXE, adulteração, permissões, concorrência e falha de gravação.

Na checagem operacional desta rodada, o receptor HTTPS/7547 e a API HTTPS/8000
estavam ativos. A X6-10 tinha Inform aceito em 27/10/2026 às 11:13:45 UTC, cerca de
12 segundos antes da consulta. Não havia operação remota ativa ou captura habilitada.
Esses processos são independentes da conversa; não há monitoramento contínuo do
assistente. O usuário utiliza a CPE para sua conexão e ela não deve ser reiniciada
para validar ferramentas offline.

## Base implementada no workspace

- GET `/api/v1/cpes/{cpe_id}/telemetry` na aplicação `acs.api.app:app`, documentado
  no OpenAPI, com leitura do PostgreSQL e autorização equivalente ao inventário.
- Observações WAN por interface: IPv4, IPv6, MAC, DNS, estado, transporte e serviço.
  Óptica: Rx/Tx com unidade ainda não verificada. Última observação e último valor
  válido têm procedência separada; múltiplos IPv6 preservam suas fontes.
- Escopos `device_info`, `discovery`, `wan`, `optical` na API/CLI de consultas.
  O GPV de telemetria depende de descoberta recente da mesma CPE/firmware e usa
  nomes permitidos congelados na operação. Uma operação ativa por CPE/sessão.
- Perfil básico V5-V2/V5R021C00S210 e fixture de Inform anonimizada, além do X6
  existente. Valores de testes WAN/ópticos são sintéticos, não prova de homologação.
- Migrations `0006_query_scopes` e `0007_wan_optical_telemetry`, aplicadas ao banco
  local antes desta revisão. Não reescrever migrations já aplicadas.
- Nenhuma dependência de produção adicionada. Domínio continua sem FastAPI/XML/ORM.

A revisão corrigiu a aceitação indevida de listas em `connection_status` e
`transport_type`, acrescentou quatro casos de teste e formatou o teste de integração
parametrizado para os dois modelos. Também explicitou a condição experimental dos
mapeamentos e a necessidade de escala óptica vinculada a perfil/revisão.

## Evidência real e pendências

| Item | Evidência disponível | Pendente |
| --- | --- | --- |
| X6-10/V5R022C00S364 | Inform e GPN/GPV de DeviceInfo validados anteriormente | Pacote WAN/óptico e homologação dos valores |
| V5-V2/V5R021C00S210 | Inform anonimizado e nove consultas de descoberta concluídas | Valores WAN/IPv6 e confirmação da escala Rx/Tx |
| API/telemetria | Testes de contrato e PostgreSQL com dados sintéticos | Validar com evidências reais capturadas |
| Captura atual | Reconstrução parcial de Inform e pacote v1 verificável offline | Execução pela API e evidências GPN/GPV |

Na verificação de 27/10, o banco local tinha zero linhas de telemetria e zero
operações ativas dentro do prazo. A descoberta recente da X6 estava `expired`.
Não deixar scripts temporários criando novas operações para tentar completar a
homologação durante o trabalho documental/offline.

O usuário informou Rx −21,14 e Tx 2,22 pela interface da X6, mas ainda não houve
comparação com resposta CWMP correspondente nem confirmação registrada da unidade.
Esses números não autorizam definir escala. `OPTICAL_DBM_SCALE` permanece `None`.
O caminho candidato IPv6 também depende de comprovação por descoberta/amostra.

A política de laboratório admite consultas para os dois perfis, mas
`profile_validated=false` e `device_support=unknown` continuam apropriados.
Não promover os mapeamentos a suporte homologado com base somente nos testes.
Quando houver conversão validada, selecionar por revisão de perfil; não habilitar
a constante global para todos os modelos. O registro declarativo de perfis ainda
é uma proposta, não substituiu o código atual de compatibilidade.

## Validação reproduzível e fechamento anterior

Executar na pasta `backend`, com PostgreSQL disponível:

```bash
UV_CACHE_DIR=/tmp/acs-uv-cache uv run --no-sync ruff check .
UV_CACHE_DIR=/tmp/acs-uv-cache uv run --no-sync ruff format --check .
UV_CACHE_DIR=/tmp/acs-uv-cache uv run --no-sync pyright
ACS_RUN_INTEGRATION_TESTS=1 UV_CACHE_DIR=/tmp/acs-uv-cache uv run --no-sync pytest -q
UV_CACHE_DIR=/tmp/acs-uv-cache uv run --no-sync acs-db-check
UV_CACHE_DIR=/tmp/acs-uv-cache uv run --no-sync alembic current
UV_CACHE_DIR=/tmp/acs-uv-cache uv run --no-sync alembic check
```

A flag `--no-sync` reutiliza o ambiente existente; em checkout novo, executar
`uv sync` antes. Os testes de integração criam schemas descartáveis; não usam a
CPE real. O teste de rede live continua opt-in. Comandos `uv` devem rodar dentro
de `backend`, que contém `pyproject.toml`.

Resultados em 27/10/2026:

| Verificação | Resultado |
| --- | --- |
| Ruff | Aprovado |
| Formatação | 138 arquivos já formatados |
| Pyright | 0 erros, 0 avisos |
| Suíte completa com PostgreSQL | 282 aprovados, 1 live opcional ignorado |
| Regressão de normalização após a correção | 28 aprovados, incluindo os quatro casos acrescentados |
| Conexão PostgreSQL | Aprovada |
| Migration local | `0007_wan_optical_telemetry (head)` |
| Alembic check | Sem novas operações de upgrade |
| Git diff check | Sem erros de whitespace |

A suíte integral começou antes do ajuste final dos campos escalares. Após esse
ajuste, foram executados novamente o arquivo `tests/unit/test_huawei_telemetry.py`,
lint, formatação e tipagem. Os 28 testes de regressão incluem casos já presentes
na suíte integral; não devem ser somados como 28 casos novos. Não houve nova
validação com a CPE real nesta rodada.

## Estado do repositório e serviços (fechamento anterior)

O HEAD na revisão era `2374a30`; as mudanças de telemetria, testes e documentação
estão no workspace, sem commit/push nesta rodada. Inspecionar `git status` antes
de versionar; esse conjunto acumulado é maior que o próximo incremento planejado.
Não versionar `.env`, `.tls`, capturas privadas ou logs. Fixtures revisadas ficam
em `tests/fixtures/cwmp`.

O receptor CWMP já estava em execução em HTTPS/7547. Esta rodada não reiniciou
serviços nem alterou configurações da CPE. Um processo Uvicorn sem reload pode
continuar usando código anterior; os testes validam os arquivos do workspace.
Na próxima validação operacional, reiniciar o receptor de forma controlada para
carregar o código vigente, sem reiniciar a CPE. Não depender de PID ou arquivos em
`/tmp` preservados entre sessões.

## Escopo concluído: pacote de evidências v1

Objetivo entregue: empacotar uma captura anonimizada já produzida pelo coletor existente
para que outro desenvolvedor possa verificá-la e usá-la offline.

Entradas: XML anonimizado do coletor atual e metadados de origem disponíveis.
Saída: diretório privado com `inform.xml` e `manifest.json` versionado. O manifesto
registra hash SHA-256/tamanho, identidade técnica permitida, firmware/hardware
quando presentes, horário de coleta quando conhecido, política de anonimização e
marcação explícita de captura parcial. Não inventar horário ou informações ausentes.
Preservar a indicação de tipos reconstruídos como strings pelo coletor atual.

Construção/verificação implementadas com biblioteca padrão e componentes
existentes. A entrada é reconstruída por lista permitida antes da publicação.
A captura atual mantém seu comportamento; a nova CLI fornece empacotamento e
verificação offline sem dependência de produção adicional.

Critérios de aceite:

1. Criar pacote a partir da fixture existente sem rede ou CPE conectada.
2. Confirmar hash, tamanho, formato, identidade técnica e indicação de parcialidade.
3. Detectar artefato adulterado/ausente e rejeitar XML inválido/XXE.
4. Não expor credenciais, identidade real ou conteúdo XML em logs e erros.
5. Não sobrescrever pacote existente; falha de publicação não deixa pacote válido
   pela metade. Testar permissões e destino conforme o adaptador de captura atual.
6. Documentar um comando para criar e outro para verificar, com exemplos sintéticos.

Fora desse incremento: endpoints novos, migrations, jobs persistentes, descoberta
GPN/GPV automática, registro declarativo de perfis, conversões ópticas e homologação
real. Esses itens compõem rodadas posteriores da arquitetura, não esta pequena entrega.

## Referências para continuar

- [Telemetria e limites atuais](WAN_OPTICAL_TELEMETRY.md).
- [Arquitetura futura de compatibilidade](CPE_COMPATIBILITY_ARCHITECTURE.md).
- [Coletor atual de Inform](INFORM_CAPTURE.md).
- Implementação atual: `src/acs/cwmp/compatibility/evidence.py` e
  `src/acs/infrastructure/storage/inform_capture.py`.
- Testes da captura: `tests/unit/test_inform_capture.py` e
  `tests/contract/test_cwmp_capture.py`.

## Arquivos desta entrega

- `src/acs/domain/operations/evidence.py`: contrato independente de infraestrutura.
- `src/acs/cwmp/compatibility/evidence_package.py`: reconstrução, manifesto e verificação.
- `src/acs/infrastructure/storage/evidence_package.py`: publicação e leitura privadas.
- `src/acs/infrastructure/storage/evidence_cli.py`: comandos locais offline.
- `src/acs/cwmp/compatibility/evidence.py`: extensão compatível para data desconhecida
  e identificação de reconstrução offline; captura original mantém seu padrão.
- `pyproject.toml`: novo entrypoint, instalado com `uv sync --offline` nesta sessão.
- `tests/unit/test_evidence_package.py`: testes do pacote e da CLI.
- `docs/EVIDENCE_PACKAGE.md`: uso, garantias e limites.

## Validação do pacote v1

Validação concluída em 27/10/2026:

- Suíte completa com PostgreSQL: **310 aprovados, 1 teste live opcional ignorado**.
- Suíte focada em pacote/captura: 43 aprovados na primeira execução; a suíte
  completa posterior também validou o ajuste no teste de falha de gravação.
- Ruff aprovado; 145 arquivos formatados; Pyright com 0 erros e 0 avisos.
- Entry point instalado via `uv sync --offline`, sem alterar dependências/lockfile.
- CLI instalada executada em subprocessos: `create` e `verify` concluídos usando
  a fixture X6 existente e um diretório temporário privado, removido após o teste.
- `git diff --check` e links da documentação verificados.

As alterações desta entrega também permanecem sem commit/push. Nenhum processo de
captura ou consulta automática foi iniciado; os serviços existentes foram preservados.

## Próximo incremento proposto

Iniciar e consultar uma coleta única de Inform pela API, com execução persistente,
janela limitada e publicação de pacote. Aproveitar os contratos deste incremento,
a autorização e o receptor existentes. Manter GPN/GPV automático, exportação de
árvore completa, conversões ópticas e registro declarativo de perfis para outras
rodadas. O plano de longo prazo continua em `CPE_COMPATIBILITY_ARCHITECTURE.md`.
