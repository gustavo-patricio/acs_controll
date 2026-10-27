# Pacote de evidências v1 — Inform parcial offline

Esta entrega acrescenta a CLI `acs-evidence` para criar e verificar um pacote
local com `inform.xml` e `manifest.json`. Funciona sem CPE conectada, banco,
`.env` ou rede. Não inicia captura, consulta remota ou processamento em background.
O receptor CWMP e a API continuam sendo processos separados dessa ferramenta.

## Criar e verificar

Na pasta `backend`, sincronize o ambiente para instalar o novo comando:

```bash
uv sync
uv run acs-evidence create \
  --input tests/fixtures/cwmp/huawei_eg8145x6_10_v5r022c00s364.xml \
  --output "$HOME/.local/share/acs/.evidence/x6-exemplo-v1"
uv run acs-evidence verify "$HOME/.local/share/acs/.evidence/x6-exemplo-v1"
```

O exemplo usa a fixture anonimizada já revisada. Para uma coleta própria,
substitua `--input` pelo arquivo gerado conforme [captura de Inform](INFORM_CAPTURE.md).
Escolha outro diretório de saída para uma nova execução: destinos existentes
nunca são sobrescritos, mesmo vazios ou links simbólicos.

O armazenamento exige filesystem Linux/WSL, diretórios privados `0700` e arquivos
`0600` do usuário atual. Evite `/mnt/c` e `/mnt/d`, onde permissões podem não ser
aplicadas. O diretório pai final é criado quando ausente; se existir com permissões
mais amplas, o comando falha sem alterá-las automaticamente.

A saída de sucesso contém somente `status`, `format_version` e `partial`:

```json
{"status": "verified", "format_version": "1", "partial": true}
```

Erros retornam código de saída diferente de zero e mensagens estáveis:
`evidence_already_exists` ou `evidence_invalid_or_unavailable`. Não incluem XML,
valores, identidade do equipamento ou caminhos de arquivos. Ambos os comandos
também podem ser executados por `uv run python -m acs.infrastructure.storage.evidence_cli`.

## Conteúdo e fidelidade

A entrada é tratada como não confiável e passa pelo parser SOAP/XML protegido
existente. O XML publicado é uma nova reconstrução por lista permitida: fabricante,
OUI, ProductClass e campos básicos de DeviceInfo. Serial, RPC ID, eventos,
CommandKey, relógio da CPE e contadores são sintéticos. Parâmetros fora da lista,
atributos e comentários arbitrários não são copiados.

O manifesto inclui:

- `format_version: "1"`, `evidence_kind: "partial-inform-reconstruction"` e
  `partial: true`;
- identidade técnica permitida e modelo/firmware/hardware quando válidos e presentes;
- nome fixo do artefato, SHA-256 e tamanho do XML publicado;
- quantidade de parâmetros retidos no pacote, sem inferir o total do envelope original;
- `received_at` extraído apenas do comentário reconhecido do coletor e
  `timestamp_source: "input-declared"`; sem esse dado, `null` e `unknown`;
- política `device-info-allowlist-v1`, tipos `reconstructed-as-xsd-string` e
  `authenticity_verified: false`.

Não usar o horário sintético `CurrentTime`, o mtime do arquivo ou o horário de
empacotamento como data de coleta. O pacote preserva apenas a data declarada pela
entrada; não comprova que ela foi autenticada pelo ACS. Campo model ausente continua
`null`: a ferramenta não aplica a inferência específica de ProductClass dos perfis.
Os campos de texto permitidos continuam declarados pela CPE; revise-os antes de
compartilhar ou promover o pacote a fixture versionada.

A verificação reconstrói o XML e compara a representação canônica e todos os campos
do manifesto. Rejeita adulteração, metadados divergentes, campos/versões desconhecidos,
chaves JSON duplicadas, arquivos ausentes, extras ou links simbólicos. Um hash válido
com atributos não permitidos no XML não basta para aprovação. Isso verifica
consistência/integridade, não assinatura ou autenticidade: quem puder substituir
ambos os arquivos ainda poderá fabricar outro pacote coerente.

## Publicação e limites

O destino é reservado com criação exclusiva de diretório. O XML e o manifesto
temporário são gravados com fsync; `manifest.json` é publicado por hard link somente
quando ambos estão completos. Ele é o marcador de conclusão. O verificador rejeita
um diretório incompleto. Falhas normais removem os arquivos produzidos e o diretório;
uma interrupção abrupta pode deixar um diretório incompleto, que nunca é considerado
um pacote válido nem é sobrescrito automaticamente. Preserve-o para diagnóstico ou
remova-o conscientemente antes de tentar de novo.

Limites: entrada/XML de até 1 MiB e limites estruturais do parser atual; manifesto
até 16 KiB; somente dois arquivos com nomes fixos. Arquivos especiais (como FIFO)
são recusados sem bloqueio. Não há importação de ZIP, caminhos fornecidos pelo
manifesto, dependências novas, alterações de banco ou endpoints novos.

## Validação e próximas entregas

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest tests/unit/test_evidence_package.py tests/unit/test_inform_capture.py tests/contract/test_cwmp_capture.py -q
```

Testes cobrem reconstrução anonimizada, data ausente, hash e metadados, XML inválido,
XXE, limites, adulteração, campos desconhecidos, publicação concorrente, permissões,
symlinks, falha de gravação, arquivos incompletos e CLI com rede bloqueada.
O coletor existente mantém a assinatura de uso e seu formato padrão.

Este pacote ainda não contém catálogo GPN, respostas GPV, tipos originais ou
homologação WAN/óptica. Criar/acompanhar coletas pela API e armazenar evidências de
consultas são entregas posteriores. Veja o [ponto de retomada](WORK_STATUS.md).
