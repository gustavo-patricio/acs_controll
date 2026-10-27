# Normalização do inventário e perfis

## Escopo entregue

As etapas 1 a 5, no escopo inicial de inventário básico, do plano em `backend_acs.md` estão
implementadas: contrato interno dos campos básicos, normalização fora do
repositório PostgreSQL, perfil genérico, [primeiro perfil Huawei](HUAWEI_PROFILE.md)
e [persistência da origem e dos horários por campo](INVENTORY_PROVENANCE.md),
com [leitura versionada pela API](INVENTORY_API.md). Capacidades ainda sem
evidência são `unknown`, e não há extensão proprietária homologada.
O fluxo continua sendo
Inform, InformResponse e POST vazio para concluir a sessão.

O adaptador HTTP injeta `ProfileInventoryNormalizer` no `InformService`. Após
autorizar a identidade da CPE, o serviço normaliza os parâmetros e entrega ao
repositório um `NormalizedInventory`. O repositório persiste os valores canônicos
sem interpretar caminhos de parâmetros ou decidir regras de fabricante.
O seletor utiliza o perfil Huawei somente na combinação validada, com retorno
ao `GenericInventoryNormalizer` nos demais casos.

## Campos e perfil

O perfil `generic-device-info`, versão `1`, utiliza uma lista explícita:

| Campo do ACS | Sufixo sob `DeviceInfo` | Tipo canônico |
| --- | --- | --- |
| `model` | `ModelName` | Texto de até 256 caracteres |
| `firmware` | `SoftwareVersion` | Texto de até 256 caracteres |
| `hardware` | `HardwareVersion` | Texto de até 256 caracteres |

São aceitos os caminhos exatos sob `Device.DeviceInfo` ou
`InternetGatewayDevice.DeviceInfo`. A correspondência diferencia maiúsculas e
minúsculas. Parâmetros proprietários e outros caminhos não são inferidos por
sufixo. Identidade, eventos e horários de sessão continuam seguindo o fluxo
existente.

O normalizador trabalha com os textos extraídos pelo parser. Não faz conversão
numérica nem usa o rótulo lexical `xsi:type` para inferir capacidades. Nenhuma
unidade física está envolvida nos três campos desta entrega.

## Regras de atualização

O contrato interno distingue:

- `valid`: texto aceito, acompanhado do caminho de origem.
- `not_reported`: nenhum caminho reconhecido foi recebido para o campo.
- `invalid`: valor recebido não pode ser aplicado, com motivo seguro e sem
  carregar o conteúdo rejeitado.

Os motivos de rejeição são `empty`, `too_long`, `control_character` e
`conflicting_values`. São rejeitados texto vazio, somente espaços, mais de 256
caracteres antes da remoção dos espaços externos e controles ASCII, incluindo
quebras de linha. Espaços externos de um texto válido são removidos.

Se os dois caminhos equivalentes, ou repetições do mesmo caminho, trouxerem
valores diferentes, o campo inteiro é inválido. Se algum candidato for inválido,
não se tenta silenciosamente outro caminho. Se todos forem válidos e equivalentes,
`Device.DeviceInfo` tem preferência para a origem. O resultado independe da ordem
da lista recebida.

Somente observações válidas substituem valores persistidos. Um Inform parcial,
um valor vazio ou uma ambiguidade não apagam o último valor válido. Para uma CPE
nova, campos sem observação válida permanecem `null`. O perfil Huawei pode obter
o modelo de `DeviceId.ProductClass` conforme suas regras explícitas. Falhas nesses campos
opcionais não rejeitam por si só um Inform válido; as regras de validação do
envelope e dos argumentos obrigatórios continuam sendo aplicadas pelo parser.

Uma repetição aceita dentro da mesma sessão continua sem duplicar os eventos ou
reescrever o inventário. Persistência de CPE, sessão e eventos permanece atômica.

## API e armazenamento

As respostas atuais de listagem e detalhe das CPEs mantêm `model`, `firmware` e
`hardware` como texto ou `null`. O endpoint adicional
`GET /api/v1/cpes/{cpe_id}/inventory` expõe os metadados sem alterar os contratos
anteriores. A migration `0004_inventory_provenance` acrescenta metadados em
`cpe_inventory_fields`, preservando as colunas e os valores existentes em `cpes`.

O resultado interno contém o perfil, sua versão, a origem de cada campo recebido
e `received_at`, obtido do relógio do ACS. Esses metadados e os motivos de invalidez
**são persistidos por campo e expostos pelo novo endpoint de inventário**. A origem
e a data do último valor válido são preservadas separadamente da última observação
inválida. `last_seen` continua sendo a última comunicação da CPE. Dados legados não
recebem metadados inventados; eles são preenchidos pelos próximos Informs válidos.

O perfil genérico não declara suporte a Wi-Fi, óptica, telefonia ou outros
recursos. Valores desconhecidos não são tratados como recursos não suportados.
Senhas e outros caminhos fora da lista permitida não entram no inventário
normalizado; valores de observações também são omitidos de sua representação
textual de diagnóstico.

## Evidências e validação

`tests/fixtures/cwmp/inform_device_inventory.xml` é uma fixture sintética, com
identidade de laboratório e segredo fictício para testar a exclusão de parâmetros
sensíveis. Não é uma captura nem evidência de homologação de uma marca. Os testes
também exercitam os caminhos `InternetGatewayDevice` e identidades distintas,
sem atribuir suporte homologado a fabricantes reais.

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest tests/unit/test_inventory_normalization.py
ACS_RUN_INTEGRATION_TESTS=1 uv run pytest -q
```

A cobertura inclui equivalência entre raízes, valores ausentes e inválidos,
conflitos, ordenação determinística, exclusão de parâmetros sensíveis,
autorização antes da normalização, atualização parcial no PostgreSQL, repetição
de Inform e inicialização real do receptor com autenticação.

## Evidência Huawei

A etapa 3 dispõe de [coleta controlada e anonimizada de Inform](INFORM_CAPTURE.md).
A primeira evidência real revisada é da Huawei EG8145X6-10, firmware V5R022C00S364,
coletada em 25/10/2026. O [perfil específico e a precedência](HUAWEI_PROFILE.md)
estão implementados, incluindo o uso restrito de `ProductClass` como modelo
quando `ModelName` não for informado.

## Próximas etapas

- Definir uma política de desatualização a partir dos horários por campo;
  observações concorrentes já respeitam a ordenação documentada na persistência.
- Preencher capacidades e acrescentar extensões com schemas próprios quando
  existirem coleta e evidência homologada por perfil.
- Ampliar as consultas remotas conforme evidências de outros campos e perfis.

O [plano original](REMOTE_COLLECTION_PLAN.md) registra a avaliação dos RPCs.
A [primeira entrega implementada](READ_QUERIES.md) consulta DeviceInfo pelo perfil
Huawei, com operações persistidas e distinção entre observações Inform e GPV.

## Coleta WAN e óptica

A entrega de endereços IPv4/IPv6, MAC, DNS e potência óptica usa descoberta
controlada e consultas por perfil. Consulte [contrato, comandos, evidências e
limitações](WAN_OPTICAL_TELEMETRY.md). O endpoint
`GET /api/v1/cpes/{cpe_id}/telemetry` está disponível no Swagger da API.

## Evolução da compatibilidade por evidências

O [desenho de referência](CPE_COMPATIBILITY_ARCHITECTURE.md) define coleta persistente,
pacotes de evidência, catálogo observado, perfis declarativos versionados e
reprodução offline. O documento distingue a base existente das próximas entregas
e estabelece critérios de homologação por campo/modelo/firmware.
