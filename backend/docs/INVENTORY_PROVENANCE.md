# Persistência da origem e dos horários do inventário

Esta entrega implementa a etapa 4 do plano de normalização. A migration
`0004_inventory_provenance` cria `cpe_inventory_fields`, com no máximo um registro
por CPE e campo (`model`, `firmware`, `hardware`). Os valores canônicos continuam
nas colunas existentes de `cpes`; a tabela nova guarda somente metadados.

A migration `0005_read_queries` acrescenta `source_method` e `operation_id`, com
equivalentes `value_source_method` e `value_operation_id` para o último valor
válido. Assim, consultas GPV mantêm a origem distinta de Inform. Veja
[consultas remotas](READ_QUERIES.md); as regras de ordenação abaixo se aplicam
a ambos os produtores.

## Dois conjuntos de informações por campo

| Colunas | Significado |
| --- | --- |
| `value_source_parameter` | Origem do último valor válido aplicado, inclusive `DeviceId.ProductClass` quando usado pelo perfil Huawei |
| `value_profile_id`, `value_profile_version` | Perfil e versão responsáveis por esse valor |
| `value_received_at` | Horário do ACS na última observação válida aplicada |
| `state`, `source_parameter` | Estado e origem da última observação recebida e aplicada ao acompanhamento |
| `profile_id`, `profile_version`, `observed_at` | Perfil, versão e horário dessa última observação |
| `invalid_reason` | Código de rejeição seguro: `empty`, `too_long`, `control_character` ou `conflicting_values` |

Um valor válido pode continuar disponível enquanto a observação mais recente é
inválida. Por isso, o perfil e o horário do valor são separados dos da observação.
Em um conflito entre caminhos, `source_parameter` pode ser `null`, pois não há
uma origem única. Não são armazenados valores rejeitados, XML bruto ou parâmetros
fora da lista permitida. Restrições no banco limitam campos, estados, motivos e
coerência dos grupos de metadados.

`value_received_at` é a última coleta válida aplicada, mesmo quando o texto não
mudou. Não é a data da última mudança de conteúdo. Todos os horários vêm do
relógio do ACS no serviço de recebimento, não de `CurrentTime` da CPE.

## Regras de atualização e concorrência

- `valid`: atualiza valor e seus metadados juntos; registra a observação válida e
  limpa o motivo de rejeição anterior.
- `invalid`: atualiza apenas a última observação e o motivo; mantém o último
  valor válido, sua origem, perfil e horário.
- `not_reported`: não cria registro nem altera qualquer metadado do campo.
  `last_seen` da CPE pode avançar independentemente dessas datas.
- Repetição aceita do Inform dentro da mesma sessão: não altera inventário,
  metadados ou eventos. O POST vazio também não atualiza os campos.

O upsert da CPE bloqueia sua linha até o commit. Todos os escritores de inventário
devem respeitar esse bloqueio. Valor canônico, metadados, sessão e eventos são
gravados na mesma transação; uma falha em qualquer etapa desfaz todo o conjunto.

A ordenação usa `received_at` por campo: somente uma observação estritamente mais
recente que `observed_at` pode alterar aquele campo. Uma observação antiga continua
podendo originar uma sessão válida, mas não retrocede os dados. Em empate, a
observação já persistida prevalece. Um campo ausente em um Inform mais novo ainda
pode receber uma observação intermediária, desde que posterior à sua própria data.

Uma observação inválida também estabelece esse limite temporal. Um valor válido
que chega depois, mas tenha horário anterior ou igual à última observação inválida,
é ignorado. Essa política conservadora evita reinterpretação de dados atrasados;
não tenta reconstruir retrospectivamente todos os valores que poderiam ter sido
aceitos em outra ordem. Os relógios dos processos ACS devem estar sincronizados.

## Registros existentes e migration

A migration não preenche a tabela nova com `last_seen`, não inventa origem ou
perfil e não altera valores ou histórico existentes. Ausência de metadados
significa origem/data ainda desconhecida, não necessariamente ausência de valor.

O próximo Inform válido fornece os metadados dos campos efetivamente observados.
Se o primeiro Inform após a migração trouxer um valor inválido, será registrado o
motivo; o valor legado permanece, com `value_*` nulo até uma nova observação válida.

Dentro de `backend/`:

```bash
uv run alembic upgrade head
uv run alembic check
```

Aplique a migration antes de iniciar o receptor com esta implementação. A API
administrativa mantém o esquema atual e continua lendo as colunas de `cpes`.
O downgrade de `0004` remove apenas os metadados novos, preservando os valores
canônicos, sessões e eventos; essa remoção perde a rastreabilidade coletada. Os
testes exercitam downgrade e upgrade somente em schemas descartáveis.

## Consulta e contrato da API

A etapa seguinte acrescentou `GET /api/v1/cpes/{cpe_id}/inventory`, descrito no
[contrato da API](INVENTORY_API.md), para ler esses metadados. Os endpoints
anteriores mantêm seu formato. Para diagnóstico direto no PostgreSQL, substitua
o UUID abaixo pelo `id` retornado no inventário:

```sql
SELECT field_name, state, invalid_reason,
       value_source_parameter, value_profile_id, value_profile_version,
       value_received_at,
       source_parameter, profile_id, profile_version, observed_at
FROM cpe_inventory_fields
WHERE cpe_id = 'UUID-DA-CPE'
ORDER BY field_name;
```

Não se infere desatualização ou falta de suporte a partir desses campos. Uma
política de idade máxima depende do uso operacional e será definida separadamente.
O armazenamento é limitado à última observação e à origem do último valor válido;
não é um histórico completo de leituras ou falhas. Uma nova leitura válida limpa
o motivo anterior, e observações antigas ignoradas não têm seus metadados guardados.

## Validação

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
ACS_RUN_INTEGRATION_TESTS=1 uv run pytest -q
```

Os testes verificam preservação de registros durante a migration, atualização
parcial, valor inválido e recuperação, origem Huawei, versões de perfil distintas,
observações fora de ordem e simultâneas, empate, repetição de sessão, ausência de
conteúdo rejeitado, restrições do banco e rollback de metadados junto dos valores.

## Validação na bancada

Em 25/10/2026, a migration foi aplicada no PostgreSQL local e `alembic check`
confirmou a correspondência entre banco e modelos. Após reiniciar o receptor,
o Inform da Huawei EG8145X6-10 / V5R022C00S364 recebido às
`2026-10-25T20:17:22.546041Z` gerou os três registros de metadados, todos válidos,
com perfil `huawei-eg8145x6-10-v5r022c00s364`, versão `1`:

| Campo | Origem persistida |
| --- | --- |
| `model` | `DeviceId.ProductClass` |
| `firmware` | `InternetGatewayDevice.DeviceInfo.SoftwareVersion` |
| `hardware` | `InternetGatewayDevice.DeviceInfo.HardwareVersion` |

As datas dos valores e observações corresponderam ao recebimento da sessão,
concluída com HTTP 200/204. Os valores existentes foram preservados: modelo
`EG8145X6-10`, firmware `V5R022C00S364` e hardware `343D.D`. Não foram enviados
RPCs adicionais nem provocadas falhas no equipamento para essa validação.
