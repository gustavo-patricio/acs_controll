# Consultas remotas por perfil

A ampliação para descoberta WAN/óptica e leitura de telemetria está descrita em
[Coleta WAN e óptica](WAN_OPTICAL_TELEMETRY.md). Os exemplos DeviceInfo abaixo
continuam válidos com `scope: "device_info"` (padrão).

Implementados `GetParameterNames` (GPN) e `GetParameterValues` (GPV) em
SOAP 1.1/CWMP 1.0, com operações assíncronas persistidas. O suporte atual é
restrito a perfis revisados. O perfil original é
`huawei-eg8145x6-10-v5r022c00s364`, versão `1`: fabricante
`Huawei Technologies Co., Ltd`, OUI `00259E`, ProductClass `EG8145X6-10` e firmware
`V5R022C00S364`. O inventário básico da EG8145V5-V2/V5R021C00S210 também
tem perfil próprio. Identidades/firmwares fora dos perfis retornam conflito; não há consulta genérica da árvore.

Referências: [TR-069 de maio de 2004, A.3.2.2 e A.3.2.3](https://www.broadband-forum.org/pdfs/tr-069-1-0-0.pdf)
e [schema oficial CWMP 1.0](https://github.com/BroadbandForum/cwmp-data-models/blob/master/cwmp-1-0.xsd).

## Escopo e fluxo

- No escopo `device_info`, GPV solicita somente `InternetGatewayDevice.DeviceInfo.ModelName`,
  `InternetGatewayDevice.DeviceInfo.SoftwareVersion` e
  `InternetGatewayDevice.DeviceInfo.HardwareVersion`.
- Nesse mesmo escopo, GPN solicita `InternetGatewayDevice.DeviceInfo.` com `NextLevel=true`, sem
  recursão. Somente os três nomes acima e seus indicadores `Writable` são retidos.
  Outros nomes são descartados; descoberta não dispara automaticamente GPV.
- A API recebe uma solicitação autenticada e registra uma operação `pending`.
  No próximo Inform, o ACS responde InformResponse. No POST vazio seguinte,
  entrega a consulta e passa a operação para `dispatched`.
- A resposta deve pertencer à mesma sessão, principal autenticado e `cwmp:ID`.
  O resultado validado é persistido; o ACS encerra essa sessão com HTTP 204.
  Nesta entrega há uma operação por sessão e uma operação ativa por CPE.
- GPV atualiza os campos canônicos e seus metadados em uma transação com o
  resultado da operação. GPN registra descoberta; não atualiza valores canônicos.

## API e Swagger

As rotas estão na aplicação administrativa `acs.api.app:app`, em `/docs`:

```text
POST /api/v1/cpes/{cpe_id}/queries
GET  /api/v1/cpes/{cpe_id}/queries/{operation_id}
GET  /api/v1/cpes/{cpe_id}/inventory
GET  /api/v1/cpes/{cpe_id}/telemetry
```

Exemplo do POST (gere um UUID novo para cada nova intenção de consulta):

```json
{
  "kind": "get_parameter_values",
  "idempotency_key": "64a87ca4-21f6-4858-b8f9-a12cefd5329c"
}
```

Para GPN, use `kind: "get_parameter_names"`. Caminhos arbitrários e campos extras
são rejeitados. O POST retorna `202` com `id`, estado, prazos, perfil, quantidade
de tentativas e resultado. Repita o GET até um estado terminal; HTTP 202, por si só,
não confirma que a CPE respondeu.

O POST exige HTTP Basic sobre HTTPS, conta administrativa com papel
`administrator` e permissão `cpe:query`. O GET de operação exige `inventory:read`
(papéis `support` ou `administrator`). A opção de inventário anônimo do laboratório
não libera nenhuma dessas duas rotas de operações. Credenciais de CPE não são
credenciais administrativas. Veja [autenticação](AUTHENTICATION.md).

Erros: `401` sem autenticação válida; `403` sem permissão ou HTTPS; `404` para
CPE/operação inexistente; `409` para perfil incompatível, outra operação ativa
ou reutilização da chave com outro tipo; `422` para entrada inválida, sem ecoar
o conteúdo recebido; `503` para indisponibilidade. Consulte `/openapi.json`.

## Idempotência, prazos e falhas

A chave é única por CPE. Reenviar a mesma chave, tipo, escopo e caminho retorna a operação original,
mesmo concluída. Uma nova consulta precisa de outra chave. Uma restrição parcial
no PostgreSQL e bloqueio da CPE impedem duas operações ativas.

O prazo pendente é dez minutos. No despacho, passa a ser o menor entre o prazo
original e sessenta segundos. A expiração é avaliada na criação, consulta de estado
e tráfego da sessão; ainda não existe varredura em background. Uma linha pode
permanecer marcada `pending`/`dispatched` até uma dessas ações, mas uma consulta
vencida não é enviada nem aplicada ao inventário.

Há uma tentativa persistida por operação. Repetir o POST vazio durante essa
tentativa devolve o mesmo RPC e ID. Repetir uma resposta concluída com o mesmo
resultado normalizado e cookie preservado retorna 204 sem nova gravação. IDs,
sessões, métodos ou resultados divergentes são rejeitados. Respostas tardias de
operações expiradas recebem 204 e são descartadas. Não há retentativa automática
após expiração/falha; solicite outra operação conscientemente.

Estados terminais: `succeeded`, `failed`, `expired`. Motivos internos incluem
`deadline_exceeded`, `profile_changed`, `unexpected_parameters`, `invalid_values`
e `cpe_fault`; não são códigos SOAP. Mudança de perfil antes da resposta invalida
a operação. Se a própria GPV revelar firmware ou modelo distinto, os valores
válidos usam o perfil genérico. Leituras inválidas preservam o último valor válido,
registram a observação inválida e fazem a operação falhar; os demais campos válidos
do conjunto podem ser atualizados.

A especificação define para GPV os faults `9001`, `9002`, `9003`, `9004`, `9005`;
para GPN, `9001`, `9002`, `9003`, `9005`. `9005` significa nome de parâmetro inválido.
O parser aceita códigos recebidos na faixa CPE `9000–9999`, inclusive extensões
de fabricante, guardando somente o número recebido, sem lhe atribuir significado
novo. Textos de fault são descartados. O ACS não inventa códigos SOAP para erros
internos; mantém os existentes `8000` (RPC não suportado) e `8003` (Inform inválido).

## Dados, segurança e arquitetura

A migration `0005_read_queries` cria `query_operations` e `query_attempts`, acrescenta
`awaiting_response` à sessão e adiciona `source_method`/`operation_id` à observação
e `value_source_method`/`value_operation_id` à procedência do último valor válido.
Observações anteriores são identificadas como Inform, sem fabricar consultas
históricas. O downgrade recusa descartar histórico existente de operações.

O inventário apresenta `source_method` e `operation_id` em `provenance` e
`last_observation`. Informs posteriores podem atualizar a procedência novamente.
`remote_queries_available=true` informa que o ACS oferece o recurso; não significa
que qualquer CPE seja elegível nem que esteja online. O perfil é verificado ao
criar e despachar. Wi-Fi, WAN, óptica e voz continuam sem capacidades homologadas.

Domínio usa dataclasses, aplicação define autorização e portas; SOAP e HTTP
permanecem adaptadores. Caminhos e interpretação Huawei ficam em
`cwmp.compatibility`. Transações PostgreSQL garantem concorrência, auditoria e
consistência entre sessão, operação e inventário. Não há dependência nova.

Limites: XML de até 1 MiB e profundidade 32, sem DTD, entidades ou recursos externos;
até 128 nomes GPN e 16 entradas no parser GPV (a política exige exatamente os
nomes solicitados; são três em DeviceInfo); nomes de até 256 caracteres; valores string de até
4096 no parser e 256 na normalização. Tipos XML são resolvidos pelo namespace.
Nenhum XML bruto, valor inválido, texto de fault, senha, usuário PPPoE ou parâmetro não
permitido é persistido nos resultados ou colocado nos logs. Logs correlacionam
requisição, sessão, CPE e operação por UUID. Na API, criação/leitura correlacionam
o usuário autenticado e a operação; rejeições registram apenas ID e status seguros.

## Validação local e na bancada

Na pasta `backend`, com PostgreSQL disponível:

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pyright
ACS_RUN_INTEGRATION_TESTS=1 uv run pytest
uv run alembic upgrade head
uv run alembic check
```

Reinicie as aplicações após a migration e use os comandos HTTPS do
[README](../README.md) e do [guia WSL](WSL_MIRRORED.md). Testes de integração usam
schemas descartáveis, sem modificar os registros da CPE de bancada.

A ferramenta local exige ambiente development/test, SQL echo desligado e acesso
privilegiado ao banco. A auditoria guarda `request_source=local` e o UID do
processo, sem impersonar um usuário administrativo HTTP:

```bash
uv run acs-query CPE_UUID --kind get_parameter_names
uv run acs-query CPE_UUID --operation-id OPERATION_UUID
# Após a primeira operação terminar:
uv run acs-query CPE_UUID --kind get_parameter_values
uv run acs-query CPE_UUID --operation-id OPERATION_UUID
```

`--key UUID` torna a solicitação local idempotente. A CLI imprime somente estado,
IDs e códigos de falha; os valores permitidos podem ser lidos pela API.
Credenciais HTTP não devem ser colocadas em comandos, URLs ou fixtures.

## Resultado da validação na bancada

Em 26/10/2026, após aplicar `0005_read_queries` e reiniciar os dois serviços HTTPS:

- GPN concluiu às 10:47:44 UTC: três nomes permitidos confirmados, todos
  `Writable=false`.
- GPV concluiu às 10:48:25 UTC: modelo `EG8145X6-10`, firmware `V5R022C00S364`
  e hardware `343D.D`.
- Ambas ficaram `succeeded`, com uma tentativa e sessão `completed`. O receptor
  registrou despacho com HTTP 200 e processamento da resposta com HTTP 204.
- A API administrativa respondeu `/health` e inventário com HTTP 200; as rotas
  de criação e acompanhamento estavam presentes em `/openapi.json`.
- A leitura posterior do inventário confirmou os valores. Informs periódicos
  seguintes atualizaram sua procedência para Inform, conforme a regra temporal.
  O resultado de cada consulta permanece na operação correspondente.

As [fixtures revisadas](../tests/fixtures/cwmp/huawei_device_info_queries.md) são
reconstruções anonimizadas dos resultados permitidos, não XML bruto capturado.
A suíte com PostgreSQL cobriu autorização, migração, concorrência, repetição,
expiração, rollback e origem GPV dos metadados; as fixtures têm testes próprios.

O erro HTTP 503 observado antes da validação coincidiu com indisponibilidade do
PostgreSQL/migration pendente. Depois da regularização, as leituras funcionaram
sem alterar a credencial da CPE. `401` indica falha de autenticação; `503` indica
indisponibilidade e não deve motivar troca de senha sem diagnóstico.

## Próximas etapas

Ampliar perfis somente com evidência anonimizada e testes: uptime com unidade,
Wi-Fi e outros perfis. A coleta WAN/óptica evolui no guia vinculado acima.
Ainda não há descoberta recursiva automática, coleta
periódica automática, retentativas, múltiplas tentativas, worker de expiração,
Connection Request, escrita de parâmetros, reboot ou firmware. Uma leitura recente
é uma observação; online/offline ainda depende da janela configurável de RN-002.

## Validação isolada de GetParameterNames em DeviceInfo

Para esta etapa, reutilize a fila e o endpoint existentes. No escopo
`device_info`, o perfil define `ParameterPath=InternetGatewayDevice.DeviceInfo.`
 e `NextLevel=true`. Não envie `parameter_path` nem `next_level` no JSON:
 o primeiro é selecionado pelo perfil e o segundo não faz parte do contrato REST.

Execute no diretório `backend`, usando o endereço HTTPS correspondente ao
certificado da API administrativa. O exemplo local abaixo solicita a senha
interativamente (a conta deve ter papel `administrator`):

```bash
export ACS_API_URL='https://localhost:8000'
export ACS_ADMIN_USER='seu_administrador'
export CPE_ID='UUID_DA_CPE_REGISTRADA'
export QUERY_KEY="$(python3 -c 'import uuid; print(uuid.uuid4())')"

curl --cacert .tls/server.crt --user "$ACS_ADMIN_USER" \
  --header 'Content-Type: application/json' \
  --data "{\"kind\":\"get_parameter_names\",\"scope\":\"device_info\",\"idempotency_key\":\"$QUERY_KEY\"}" \
  "$ACS_API_URL/api/v1/cpes/$CPE_ID/queries"
```

Se a API usa outra autoridade certificadora, substitua `--cacert` pelo certificado
 dessa autoridade. Use o `id` retornado no HTTP 202 para consultar:

```bash
export OPERATION_ID='UUID_RETORNADO_NO_POST'
curl --cacert .tls/server.crt --user "$ACS_ADMIN_USER" \
  "$ACS_API_URL/api/v1/cpes/$CPE_ID/queries/$OPERATION_ID"
```

O fluxo esperado é `pending` → Inform/InformResponse → POST vazio/GPN →
GPNResponse/HTTP 204 → `succeeded`. O resultado contém os nomes permitidos
`ModelName`, `SoftwareVersion`, `HardwareVersion` e seus indicadores `writable`;
esta operação não coleta os valores desses parâmetros. Não envia requisições à
`ConnectionRequestURL`, não dispara outra consulta e não percorre a árvore
recursivamente. Uma CPE com perfil incompatível recebe conflito na criação.

SOAP Fault correlacionado termina em `failed`, com `failure_reason=cpe_fault` e
código recebido preservado (o simulador usa 9005: nome inválido). Sem resposta no
prazo, a operação termina em `expired`, com `deadline_exceeded`. Respostas tardias
são descartadas e a sessão termina com 204. A expiração é avaliada no tráfego ou
na consulta de estado; não há worker de expiração periódica.

Teste com simulador HTTP e PostgreSQL isolado:

```bash
ACS_RUN_INTEGRATION_TESTS=1 uv run pytest -q \
  tests/integration/test_read_queries.py \
  tests/unit/test_query_soap.py tests/unit/test_cpe_simulator.py
```

O simulador valida o InformResponse, o caminho e NextLevel do GPN, responde com
XML próprio e cobre sucesso, SOAP Fault e resposta após timeout. Os testes
existentes também verificam ID incorreto, principal incorreto, repetição e
persistência. Nenhuma CPE real precisa ser reiniciada para esses testes.

## Catálogo persistente por CPE

`GET /api/v1/cpes/{cpe_id}/queries/catalog?prefix=InternetGatewayDevice.DeviceInfo.&limit=50&offset=0`
consulta os nomes observados, com a mesma autenticação administrativa das consultas
(`inventory:read`). Não dispara nenhum RPC. Está disponível no Swagger.

A resposta contém `items`, `limit`, `offset` e `has_more`. Cada item contém apenas
`cpe_id`, `path` (completo), `is_object`, `writable`, `observed_at` e `operation_id`.
O ponto final do caminho identifica um objeto. GPN não fornece tipo nem valor;
esses campos não existem neste catálogo. `observed_at` é a data de recebimento
no ACS, não um relógio informado pela CPE.

O prefixo é literal, inclusive para `%` e `_`. A ordenação é por caminho; `limit`
aceita 1–100 (padrão 50) e `offset` começa em zero. Páginas consultadas durante
novas gravações não constituem um snapshot. CPE existente sem observações retorna
lista vazia; CPE inexistente, 404; parâmetros inválidos, 422.

A migration `0009_parameter_catalog` cria a chave única `(cpe_id, path)` e
recupera a observação mais recente de cada caminho retido nas operações GPN
bem-sucedidas anteriores. Caminhos descartados pelo código antigo não podem ser
recuperados. Novas respostas GPN válidas e correlacionadas gravam todos os nomes
estruturais recebidos dentro do escopo solicitado, independentemente da projeção
do resultado da operação. Nenhuma regra de perfil nova foi adicionada.

A gravação ocorre na mesma transação da conclusão da operação. Outra observação
atualiza Writable, data e operação de origem; uma repetição da mesma resposta não
altera o catálogo. Respostas parciais ou vazias não removem caminhos antigos.
Faults, respostas inesperadas e respostas após timeout não atualizam o catálogo.
Ausência de um caminho não comprova que ele deixou de existir na CPE.

```bash
uv run alembic upgrade head
ACS_RUN_INTEGRATION_TESTS=1 uv run pytest -q \
  tests/integration/test_parameter_catalog.py tests/integration/test_read_queries.py
```

O downgrade remove o catálogo, incluindo caminhos novos que não foram retidos na
projeção histórica das operações; exporte-o antes de executar esse downgrade.

## GPV com caminhos selecionados

O mesmo POST aceita `parameter_names`, uma lista não vazia de até 16 caminhos
únicos. Nesta etapa, a política permite somente as folhas `ModelName`,
`HardwareVersion` e `SoftwareVersion` de `InternetGatewayDevice.DeviceInfo.`.
Objetos, credenciais e demais caminhos são rejeitados; não se lê a árvore inteira.
A seleção explícita exige `kind=get_parameter_values` e `scope=device_info`.
Omissão da lista mantém o comportamento anterior do conjunto fixo por perfil.

No diretório `backend`, configure `ACS_API_URL`, `ACS_ADMIN_USER` e `CPE_ID` como
no exemplo anterior. A chamada exata para hardware e software é:

```bash
curl --cacert .tls/server.crt --user "$ACS_ADMIN_USER" \
  --header 'Content-Type: application/json' \
  --data "{\"kind\":\"get_parameter_values\",\"scope\":\"device_info\",\"idempotency_key\":\"$(python3 -c 'import uuid; print(uuid.uuid4())')\",\"parameter_names\":[\"InternetGatewayDevice.DeviceInfo.HardwareVersion\",\"InternetGatewayDevice.DeviceInfo.SoftwareVersion\"]}" \
  "$ACS_API_URL/api/v1/cpes/$CPE_ID/queries"
```

O HTTP 202 cria a operação pendente. Consulte seu `id` no GET de operação existente.
O ACS aguarda Inform/InformResponse e o POST vazio da mesma sessão para enviar o
GPV. A resposta deve corresponder à sessão, CPE, ID e conjunto exato solicitado;
nenhum RPC é enviado para ConnectionRequestURL. Reordenar a lista com a mesma chave
é idempotente; mudar seu conteúdo retorna 409. Lista vazia ou maior que o limite
retorna 422; duplicatas e caminhos não permitidos, 409.

A migration `0010_parameter_values` cria `cpe_parameter_values`, com chave
`(cpe_id, path)`, `xml_type`, `value`, `observed_at` e `operation_id`. O tipo XML é
resolvido pelo namespace, usando a grafia canônica `xsd:string`, não o prefixo
arbitrário escolhido pelo equipamento. Para essas folhas DeviceInfo, a política
aceita strings imprimíveis de até 256 caracteres, inclusive vazias, preservando
espaços e conteúdo sem conversão. Tipo incompatível ou conteúdo fora da política
faz a consulta explícita falhar. Os valores nunca são incluídos nos logs.

Cada resposta válida atualiza os valores selecionados na mesma transação da
operação. Fault, timeout, resposta incompleta/inesperada ou inválida preservam a
última observação válida; não há gravação de null. A seleção explícita armazena
observações por caminho, sem atualizar automaticamente o inventário normalizado:
string vazia válida em GPV não significa identificação válida para o inventário.
O catálogo GPN permanece separado e não recebe tipos/valores inventados.

Não há backfill desta tabela: resultados normalizados antigos não guardavam o tipo
XML reportado nem garantiam preservar a string original. Não se presume esses
dados. O downgrade remove as observações; exporte-as antes de executá-lo.

```bash
uv run alembic upgrade head
ACS_RUN_INTEGRATION_TESTS=1 uv run pytest -q \
  tests/integration/test_parameter_values.py tests/integration/test_read_queries.py \
  tests/integration/test_telemetry.py tests/unit/test_query_soap.py \
  tests/unit/test_query_evidence.py
```

## Trabalhos limitados

A navegação por ramos com GPN e a seleção de folhas em lotes GPV são descritas em
[Descoberta e lotes limitados](BOUNDED_COLLECTIONS.md). Operações avulsas continuam
válidas; descoberta não dispara leitura automática de valores.

## Exportação de resultados armazenados

A exportação de catálogo, últimas observações tipadas, escopo e falhas em JSON ou
XML reconstruído está em [Exportação de resultados](STORED_RESULTS_EXPORT.md).
