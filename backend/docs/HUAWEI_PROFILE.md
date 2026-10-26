# Primeiro perfil Huawei de inventário

O perfil `huawei-eg8145x6-10-v5r022c00s364`, versão `1`, implementa a etapa 3 do
plano de normalização. A referência é a [fixture real anonimizada da bancada](../tests/fixtures/cwmp/huawei_eg8145x6_10_v5r022c00s364.md).
O escopo é o inventário básico recebido por Inform; não declara suporte a Wi-Fi,
óptica, telefonia, parâmetros proprietários ou RPCs adicionais.

## Seleção e precedência

O `ProfileInventoryNormalizer` é injetado no `InformService` durante a
inicialização do receptor CWMP. Primeiro ele valida os campos pelas regras
genéricas, depois procura um perfil específico. A autorização da CPE acontece
antes da normalização. A seleção não consulta o banco nem usa perfil ou firmware
de um Inform anterior.

Todas as condições abaixo precisam ser satisfeitas no Inform atual:

| Critério | Valor |
| --- | --- |
| Versão CWMP | `1.0` |
| `DeviceId.Manufacturer` | `Huawei Technologies Co., Ltd` |
| `DeviceId.OUI` | `00259E` |
| `DeviceId.ProductClass` | `EG8145X6-10` |
| Firmware normalizado e válido | `V5R022C00S364` |
| Caminho de firmware presente | `InternetGatewayDevice.DeviceInfo.SoftwareVersion` |

Identidade e produto usam igualdade exata, sem inferência por prefixo ou nome
comercial. A limpeza de espaços e a validação de firmware seguem as regras do
perfil genérico. Firmware conflitante entre raízes, inválido ou ausente não
seleciona o perfil Huawei. Se houver `ModelName` válido e diferente do produto
previsto, também se utiliza o genérico.

Os perfis têm prioridade explícita: o maior número é avaliado primeiro. Este
perfil usa `100`. Perfis futuros mais específicos deverão receber uma prioridade
superior, acompanhada de evidência e testes. Identificadores ou prioridades
duplicados impedem a construção do seletor, evitando decisões dependentes da
ordem de cadastro. O genérico é o retorno quando nenhum perfil corresponder.

O hardware observado na bancada foi `343D.D`. Ele é um dado normalizado, não um
critério de seleção desta versão. Isso não declara homologação de operações em
outras revisões de hardware. A troca de firmware exige nova evidência antes de
ampliar os critérios do perfil.

## Regra de modelo e valores preservados

Na fixture real, `ProductClass` identifica `EG8145X6-10` e `ModelName` não consta.
Para essa combinação específica, o ACS adota a seguinte regra de inventário:

- Se `ModelName` não foi informado, `model` recebe `DeviceId.ProductClass` e sua
  observação interna registra essa origem.
- Se `ModelName` veio válido e corresponde ao produto esperado, seu valor e
  caminho de origem são preservados.
- Se `ModelName` veio vazio, inválido ou conflitante, não se usa `ProductClass`
  para esconder a falha. O último modelo válido persistido é preservado.
- Firmware e hardware mantêm o mapeamento, validação e tratamento de conflitos
  genéricos. Não são criados valores para campos ausentes.

Tratar `ProductClass` como modelo é uma decisão explícita deste perfil de
inventário. Não é uma equivalência presumida para todas as CPEs ou para todos os
produtos Huawei. A fixture mantém os dados originais anonimizados: o modelo não
foi inserido artificialmente no XML.

Um Inform parcial sem firmware volta ao genérico e não apaga os valores
persistidos. Um novo firmware válido é armazenado pelo genérico, enquanto o último
modelo conhecido permanece no inventário se nenhum novo modelo válido for
informado. Isso não significa que o firmware novo esteja homologado. As datas e
origens por campo seguem a [persistência de metadados](INVENTORY_PROVENANCE.md).

## API, banco e diagnóstico

Após o próximo Inform correspondente ao perfil, `GET /api/v1/cpes` e
`GET /api/v1/cpes/{id}` passam a mostrar `model: "EG8145X6-10"` para a CPE de
bancada. As respostas mantêm o contrato atual. O perfil não exigiu alteração de
schema; a entrega seguinte acrescentou a migration `0004_inventory_provenance`
para guardar metadados. Registros existentes evoluem por novos Informs.

O evento estruturado `inventory_normalized` registra somente `profile_id`,
`profile_version`, `session_id` e `cpe_id`, permitindo correlação com
`inform_accepted` e `session_completed`. É emitido após a persistência de uma nova
sessão. Repetir o Inform na mesma sessão não duplica esse evento nem reescreve o
inventário. Valores de parâmetros, serial e credenciais não são registrados.

Perfil, versão, origem e horários agora são persistidos por campo pela migration
`0004_inventory_provenance` e expostos por
[`GET /api/v1/cpes/{cpe_id}/inventory`](INVENTORY_API.md). O evento de log
identifica o perfil avaliado para a sessão; observações antigas podem ser ignoradas
pela ordenação da persistência, sem retroceder os dados.

## Validação

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest tests/unit/test_huawei_profile.py
ACS_RUN_INTEGRATION_TESTS=1 uv run pytest -q
```

Os testes usam a fixture real revisada e variações sintéticas para cobrir seleção,
precedência, retorno ao genérico, identidade desconhecida, mudança/ausência/conflito
de firmware, modelo inválido, exclusão de valores dos logs e autorização antes da
normalização. A integração PostgreSQL cobre inicialização real do receptor,
atualização de CPE existente, repetição sem duplicação, POST vazio e leitura por
listagem e detalhe da API administrativa.

## Resultado na CPE real

Em 25/10/2026, após reiniciar o receptor com o seletor, o Inform periódico da
CPE de bancada selecionou `huawei-eg8145x6-10-v5r022c00s364`, versão `1`.
Os logs da mesma sessão confirmaram `inventory_normalized`, InformResponse com
HTTP 200 e conclusão por POST vazio com HTTP 204. A coleta de XML permaneceu
desativada; nenhum RPC adicional foi enviado ao equipamento.

A leitura HTTPS da API administrativa em execução retornou HTTP 200 e confirmou
`model: EG8145X6-10`, `firmware: V5R022C00S364`, `hardware: 343D.D` e
`last_seen: 2026-10-25T19:50:02.469953Z`. Antes da atualização, `model` era `null`.
Isso valida o mapeamento básico dessa bancada e desse firmware, sem ampliar a
homologação para outras combinações.

Em 26/10/2026, a primeira entrega de [consultas remotas](READ_QUERIES.md) validou
GPN e GPV nessa mesma combinação de modelo/firmware. GPV confirmou `ModelName`,
`SoftwareVersion` e `HardwareVersion`; as fixtures dos resultados permitidos
têm [origem e limites documentados](../tests/fixtures/cwmp/huawei_device_info_queries.md).
