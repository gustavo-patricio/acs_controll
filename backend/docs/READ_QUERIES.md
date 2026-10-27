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
