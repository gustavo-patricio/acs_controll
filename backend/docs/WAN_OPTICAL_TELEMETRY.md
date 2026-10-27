# Coleta WAN Internet/PPPoE e potência óptica

Esta entrega acrescenta descoberta controlada de nomes, leitura de parâmetros
permitidos e `GET /api/v1/cpes/{cpe_id}/telemetry` na API administrativa e no Swagger.
O endpoint lê o PostgreSQL; solicitar a página não envia comandos ao equipamento.
A coleta usa exclusivamente GPN/GPV, uma operação por sessão, na próxima conexão
iniciada pela CPE. Não altera WAN, autenticação PPPoE ou configuração óptica.

## Estado da entrega e homologação

A implementação está disponível para testes de laboratório; a coleta real de
WAN/óptica ainda não está homologada. A política admite EG8145V5-V2/V5R021C00S210
e EG8145X6-10/V5R022C00S364, exige descoberta prévia e mantém capacidades sem
homologação declarada. Elegibilidade da consulta não comprova os mapeamentos.

- V5-V2: existe Inform anonimizado e descoberta real de alguns objetos WAN/ópticos.
  Não há validação real concluída dos valores WAN/IPv6 ou da escala óptica.
- X6-10: DeviceInfo já foi validado em bancada; a tentativa recente de descoberta
  WAN expirou. Os testes de telemetria desse perfil usam valores sintéticos.
- A regra candidata de IPv6 exige o caminho descoberto
  `X_HW_IPv6.IPv6Address.{i}.IPAddress`; ainda precisa de confirmação na bancada.
- Rx/Tx permanecem `unverified`, sem valor canônico ou unidade presumida.
  Uma futura escala deverá ser vinculada a perfil/revisão, nunca habilitada
  globalmente para todos os equipamentos Huawei.

A evolução será feita pelo [coletor de evidências e reprodução offline](CPE_COMPATIBILITY_ARCHITECTURE.md).
O [ponto de retomada](WORK_STATUS.md) registra verificações e o próximo incremento.

## Contrato para o frontend

A resposta contém `schema_version: "1"`, `cpe_id`, `wan` e `optical`. Cada elemento
possui `interface_path` e `fields`. A WAN inclui `ipv4`, `ipv6`, `mac`, `dns`,
`connection_status`, `service_list` e `transport_type`; óptica inclui `rx_power`
e `tx_power`. Endereços e DNS são listas, MAC é uma string normalizada. Potências
canônicas somente são números com unidade `dBm` após confirmação da escala do
modelo/firmware. Não se presume que uma instância fixa seja a WAN de Internet.

Cada campo separa:

- `value`, `unit` e `provenance`: último valor válido, suas fontes, perfil, versão,
  horário e operação de origem;
- `state`, `reason`, `observed_at`, `source_parameter`, `source_parameters` e
  `operation_id`: resultado da última observação, mesmo quando não atualizou valor.

`valid` indica leitura validada; `unavailable` indica vazio/endereço não atribuído;
`invalid` indica conteúdo rejeitado; `unverified` indica unidade não confirmada;
`not_collected` indica ausência de coleta. Valores vazios ou inválidos preservam
o último valor válido e sua data. O frontend deve exibir esse valor como anterior
quando o estado atual não for `valid`. Ausência não prova falta de suporte.
As fontes de todos os endereços IPv6 agregados são preservadas; não se atribui o
conjunto a um único parâmetro. Interfaces são mantidas separadamente.

No inventário, `acs_implemented=true` para WAN/óptica indica a implementação
da coleta no ACS. `device_support=unknown` e `profile_validated=false` não são
promovidos somente pela presença do modelo ou por uma descoberta de nomes.

A leitura segue a autorização do inventário (`inventory:read`), incluindo a opção
explícita de leitura anônima em desenvolvimento. Agendar e consultar operações
continua exigindo autenticação administrativa HTTPS. Respostas usam `no-store`.
Erros do endpoint: 401/403, 404 CPE inexistente, 422 UUID inválido, 503 indisponível.

## Descoberta e agendamento

O POST existente `/api/v1/cpes/{cpe_id}/queries` aceita `scope` e `parameter_path`:

| Escopo | RPC | Caminho |
| --- | --- | --- |
| `device_info` (padrão) | GPN/GPV | Omitido; comportamento anterior |
| `discovery` | GPN | Subárvore permitida com ponto final |
| `wan` | GPV | Instância WANPPPConnection descoberta |
| `optical` | GPV | Instância óptica permitida e descoberta |

Exemplo de descoberta, com nova chave para cada intenção:

```json
{
  "kind": "get_parameter_names",
  "scope": "discovery",
  "parameter_path": "InternetGatewayDevice.WANDevice.",
  "idempotency_key": "126b1e67-e65e-4e90-bfa4-cbcfdc548461"
}
```

Aguarde `succeeded` no GET da operação. Desça somente pelas instâncias retornadas,
um nível por operação (`NextLevel=true`), até identificar a WANPPPConnection e o
objeto óptico. GPN conserva objetos e uma lista fechada de nomes relevantes;
nomes como Password e Username são descartados. Não há descoberta recursiva
automática, nem GPV de uma subárvore inteira.

Para coletar uma WAN previamente descoberta:

```json
{
  "kind": "get_parameter_values",
  "scope": "wan",
  "parameter_path": "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.",
  "idempotency_key": "a26506a2-8387-4b3b-b2bf-c683ce76142f"
}
```

O número `1` é ilustrativo; use as instâncias do equipamento. O servidor monta os
nomes por lista permitida intersectada com descoberta bem-sucedida dessa CPE e
firmware nas últimas 24 horas. Considera até 256 operações recentes, mantendo só
a descoberta mais recente de cada caminho. A lista exata é congelada na operação.
A resposta deve conter exatamente os nomes pedidos. `TransportType=PPPoE` e
`X_HW_SERVICELIST` contendo `INTERNET` são obrigatórios para aceitar a interface.
Se essa classificação falhar, os dados não entram na telemetria WAN.

A chave de idempotência compara tipo, escopo e caminho. Conflitos de descoberta,
perfil ou operação ativa retornam 409. Mantêm-se prazos de dez minutos pendente,
sessenta segundos após despacho, uma tentativa e nenhuma retentativa automática.
Os motivos internos `discovery_required`, `internet_classification_required`,
`not_internet_pppoe`, `invalid_values` e `unverified_units` não são faults CWMP.
Nenhum código de fault novo foi introduzido.

CLI equivalente, dentro de `backend`:

```bash
uv run acs-query CPE_UUID --kind get_parameter_names --scope discovery --path InternetGatewayDevice.WANDevice.
uv run acs-query CPE_UUID --operation-id OPERATION_UUID
# Após descoberta e término da operação anterior:
uv run acs-query CPE_UUID --kind get_parameter_values --scope wan --path CAMINHO_WAN_PPP
uv run acs-query CPE_UUID --kind get_parameter_values --scope optical --path CAMINHO_OPTICO
```

A CLI registra o UID local e imprime somente estado/IDs; valores estão disponíveis
na API autenticada de operações. Os resultados ópticos para calibração contêm
apenas números brutos validados sintaticamente, sem pressupor dBm. Uma operação
com unidade não verificada termina `failed/unverified_units`, preserva a
observação `unverified` e não publica potência canônica.

## Persistência e validação

`0006_query_scopes` acrescenta escopo/caminho às operações existentes.
`0007_wan_optical_telemetry` acrescenta nomes solicitados e a tabela
`cpe_telemetry_fields`, com chaves por CPE/interface/campo, origem da última
observação e do último valor válido. Sessão, operação e telemetria são atualizadas
na mesma transação sob bloqueio da CPE. Respostas repetidas não regravam leituras;
observações antigas não substituem novas. As migrations recusam downgrade que
apagaria histórico de operações desses escopos.

O domínio permanece independente de FastAPI/XML/SQLAlchemy. A compatibilidade
Huawei seleciona caminhos e normaliza valores; o adaptador SOAP valida tipos e
limites; a aplicação autoriza; o PostgreSQL coordena consistência e concorrência.
Não foram acrescentadas dependências. Nenhum valor de IP, DNS, MAC, potência,
credencial ou XML bruto é escrito em logs. Valores inválidos são descartados.

Limites: SOAP 1.1/CWMP 1.0, XML seguro já existente, GPN até 128 nomes, GPV até
16 parâmetros, DNS até oito endereços, caminhos até 256 caracteres e profundidade
de descoberta limitada. Tipos GPV aceitos: string, int, unsignedInt e boolean;
intervalos numéricos são validados. DeviceInfo continua exigindo strings.

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
ACS_RUN_INTEGRATION_TESTS=1 uv run pytest -q
uv run alembic upgrade head
uv run alembic check
```

A suíte cobre normalização, seleção de perfil, tipos SOAP, descoberta, autorização,
Swagger, PostgreSQL, repetição, classificação Internet/PPPoE e preservação do último
valor válido. Testes usam schemas descartáveis e endereços de documentação.

## Limitações

Não há atualização periódica automática, orquestrador de descoberta, Connection
Request, WAN bridge/IP, usuário PPPoE, VLAN, gateway, DHCPv6-PD, histórico gráfico
ou remoção automática de interfaces antigas. Campos ausentes permanecem sem
coleta. A homologação de cada caminho e da escala óptica depende do modelo e
firmware testados; evidência de um modelo não homologa outro.
