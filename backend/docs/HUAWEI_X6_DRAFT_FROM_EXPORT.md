# Rascunho de mapeamentos da EG8145X6-10

Fonte principal: [`huawei_x6_stored_results_anonymized.json`](../tests/fixtures/cwmp/huawei_x6_stored_results_anonymized.json), exportação parcial dos resultados já armazenados em 28/10/2026. Identidade observada: `Huawei Technologies Co., Ltd`, ProductClass `EG8145X6-10`, firmware `V5R022C00S364`. A fixture não contém serial real, credenciais nem valores de rede.

A exportação contém nove nomes de catálogo e três observações de GPV antigas marcadas `typed_observation_not_retained`. Esses valores antigos não comprovam o tipo XML original. Como segunda evidência, o [Inform anonimizado da bancada](../tests/fixtures/cwmp/huawei_eg8145x6_10_v5r022c00s364.md) registra ProductClass, SoftwareVersion e HardwareVersion, mas informa que os **tipos XML da fixture são sintéticos**. A reconstrução de GPV em `huawei_device_info_values.xml` também não prova o tipo original. Nenhuma das fontes autoriza inferir escala óptica.

| Campo interno | Caminho ou regra | Tipo e conversão | Evidência | Situação |
| --- | --- | --- | --- | --- |
| `model` | `DeviceId.ProductClass` somente se `ModelName` ausente, regra já existente no perfil X6 | Identificador textual validado; tipo XML não aplicável ao DeviceId | identidade da exportação + Inform anonimizado | **comprovado** para esta identidade; tipo de `ModelName` pendente |
| `firmware` | `InternetGatewayDevice.DeviceInfo.SoftwareVersion` | Texto validado pelo normalizador genérico; **tipo XML original desconhecido**, sem conversão de unidade | nome no catálogo + valor no Inform anonimizado | **comprovado** quanto ao caminho/significado; tipo pendente |
| `hardware` | `InternetGatewayDevice.DeviceInfo.HardwareVersion` | Texto validado pelo normalizador genérico; **tipo XML original desconhecido**, sem conversão de unidade | nome no catálogo + valor no Inform anonimizado | **comprovado** quanto ao caminho/significado; tipo pendente |

A situação refere-se ao **mapeamento**, não à homologação integral do equipamento. Os detalhes de tipo ainda pendentes são explícitos em cada `DraftMapping.pending`. `ModelName` aparece no catálogo, porém o Inform analisado não o contém; permanece fora da regra nova. Não alteramos a normalização genérica nem a precedência ativa.

A exportação mostra objetos concretos `InternetGatewayDevice.WANDevice.1.*`, inclusive `WANConnectionDevice.` e `X_GponInterafceConfig.`. Isso comprova apenas a existência dessas instâncias naquele equipamento e naquela observação. Não há folhas/valores/semântica suficientes para mapear WAN, Wi-Fi, sinal óptico ou campos proprietários. Por isso o rascunho não contém caminhos desses campos, índices fixos, unidades ou escalas. Uma descoberta expirou (`deadline_exceeded`), reforçando que o catálogo é parcial.

`ProfileInventoryNormalizer.draft_from_export()` constrói o objeto de revisão e exige identidade exata e os dois nomes DeviceInfo comprovados. O objeto tem `approval="rascunho"` e **não é incluído** em `ProfileInventoryNormalizer().profiles`. O parser SOAP permanece genérico e separado em `acs.cwmp.soap`; o rascunho opera sobre o JSON já anonimizado e não dispara RPCs.

Validação:

```bash
uv run pytest -q tests/unit/test_draft_profile.py tests/unit/test_huawei_profile.py
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

Antes de ampliar ou aprovar o perfil, precisamos de GPV tipado real para os caminhos selecionados, descoberta completa das folhas relevantes e comparação de unidades com a interface do equipamento. A aprovação deve ser uma ação explícita posterior.
