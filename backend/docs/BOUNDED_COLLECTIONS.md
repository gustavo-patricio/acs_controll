# Descoberta por ramos e leitura selecionada em lotes

Um trabalho persistido agrupa operações da fila existente. O ACS continua enviando
um RPC por sessão CWMP: Inform/InformResponse, POST vazio/RPC e resposta/204. A
próxima operação fica pendente para outra sessão. Nenhuma chamada é enviada para
ConnectionRequestURL. API, receptor e PostgreSQL precisam permanecer disponíveis;
o acompanhamento não depende de manter a conversa com o assistente aberta.

## API

As rotas estão no Swagger da API administrativa:

- `POST /api/v1/cpes/{cpe_id}/queries/collections`: cria um trabalho (202), exige
  `cpe:query` e HTTP Basic sobre HTTPS.
- `GET /api/v1/cpes/{cpe_id}/queries/collections/{job_id}`: consulta progresso,
  exige `inventory:read`. Respostas usam `Cache-Control: no-store`.

Exemplo de descoberta (gere outra chave para cada nova intenção):

```json
{
  "kind": "discovery",
  "root": "InternetGatewayDevice.WANDevice.",
  "max_depth": 3,
  "max_rpcs": 20,
  "max_entries": 1000,
  "idempotency_key": "e1d7116f-764b-4ff7-989a-080ae3da3d45"
}
```

O ACS consulta a raiz com `NextLevel=true`, guarda as entradas válidas no catálogo
e enfileira somente objetos concretos retornados, em largura e ordem de caminho.
Não deriva instâncias de modelos: `{i}`, curingas e caminhos fora do ramo são
rejeitados. Esta ampliação trata nomes estruturais, sem adicionar perfis de valores.
A elegibilidade de fabricante/modelo/firmware existente continua valendo.

Após consultar o catálogo, selecione explicitamente folhas para leitura:

```json
{
  "kind": "values",
  "parameter_names": [
    "InternetGatewayDevice.DeviceInfo.HardwareVersion",
    "InternetGatewayDevice.DeviceInfo.SoftwareVersion"
  ],
  "batch_size": 1,
  "max_rpcs": 10,
  "max_entries": 100,
  "idempotency_key": "924549bd-bb59-445a-908a-d49b9c964f2b"
}
```

A seleção deve existir como folha no catálogo da mesma CPE. Estar no catálogo não
libera leitura de dados sensíveis: a política atual de GPV selecionado continua
restrita a ModelName, HardwareVersion e SoftwareVersion de DeviceInfo. Nenhum GPV
é criado automaticamente após a descoberta. A lista é ordenada, dividida em lotes
e validada integralmente antes de criar a primeira operação. Não há placeholders.

Use os mesmos parâmetros de TLS e autenticação dos exemplos de
[READ_QUERIES.md](READ_QUERIES.md), mudando a rota e o JSON. A mesma chave com a
mesma especificação retorna o trabalho original, inclusive terminado; outra
especificação retorna 409. Há um trabalho ativo por CPE; uma operação avulsa ou
outro trabalho concorrente retorna 409. Repetições de operações já criadas mantêm
a idempotência anterior. Limites inválidos retornam 422; seleção incompatível,
409; trabalho de outra CPE ou inexistente, 404.

## Limites e progresso

Configuração por trabalho, persistida junto da solicitação:

| Campo | Padrão | Faixa | Significado |
|---|---:|---:|---|
| `max_depth` | 3 | 0–16 | Profundidade de objetos consultados; raiz = 0 |
| `max_rpcs` | 20 | 1–128 | Máximo de operações criadas, incluindo as que falham/expiram |
| `max_entries` | 1000 | 1–4096 | Máximo de entradas observadas/valores bem-sucedidos processados pelo trabalho |
| `batch_size` | 8 | 1–16 | Máximo de folhas solicitadas por GPV |

`max_depth=0` consulta apenas a raiz, sem descer aos objetos retornados. O limite de
entradas restringe a gravação no catálogo pelo trabalho; o ACS só conhece o tamanho
da resposta depois de recebê-la. Mantém-se o limite de segurança do parser GPN de
128 entradas por resposta e os limites de tamanho XML existentes. Exceder esse
limite não libera parsing irrestrito; a operação poderá expirar. Resultados de
operação mantêm sua projeção anterior, limitada pelo parser.

O GET apresenta `state`, `spec`, `rpc_count`, `completed_count`, `entry_count`,
`pending_count`, `operation_ids`, `failures`, `limits_reached`, `created_at` e
`finished_at`. `pending_count` conta os passos ainda na fila interna, sem a operação
corrente. Os IDs permitem consultar os resultados individuais nas rotas existentes.
Falhas incluem operação de origem e motivo seguro; incompatibilidades de ramo
antes da criação de uma operação têm apenas o motivo.

Estados:

- `running`: existe trabalho a executar.
- `succeeded`: todos os passos admitidos terminaram sem falha ou limite atingido.
- `partial`: houve fault, timeout, resposta inválida, ramo não suportado ou limite
  que impediu completar a navegação/seleção. Uma falha total também usa `partial`,
  com zero entradas e as falhas registradas.

Falhar em um ramo/lote não descarta os anteriores nem impede os demais dentro dos
limites. Repetir a resposta não duplica progresso. A expiração da operação permanece
em dez minutos pendente/sessenta segundos após despacho. A retomada ocorre ao
receber tráfego CWMP ou consultar o trabalho; não há worker de polling periódico.
Se a CPE ficar offline e ninguém consultar, o estado persistido pode ficar desatualizado
até a próxima dessas ações. Reiniciar os processos não perde a fila ou os limites.

Descoberta é uma observação parcial e temporal, não um snapshot da árvore. Não se
apagam caminhos ausentes nem valores anteriores quando um ramo ou lote falha.
Strings e tipos XML seguem a política e persistência já implementadas para GPV.

## Migration e validação

`0011_collection_jobs` adiciona trabalhos e progresso sem recriar a fila de RPCs.
O downgrade remove esse acompanhamento: exporte-o antes de reverter.

```bash
uv run alembic upgrade head
ACS_RUN_INTEGRATION_TESTS=1 uv run pytest -q \
  tests/integration/test_collection_jobs.py \
  tests/integration/test_parameter_catalog.py tests/integration/test_parameter_values.py \
  tests/integration/test_read_queries.py tests/integration/test_telemetry.py \
  tests/unit/test_query_soap.py tests/unit/test_query_evidence.py
```

Os testes usam schemas PostgreSQL descartáveis; não disparam descoberta na CPE real.
Cobrem navegação, retomada, limites, placeholders, faults/timeouts, conclusão parcial,
preservação de dados, seleção de folhas, autorização, idempotência e divisão em lotes.
