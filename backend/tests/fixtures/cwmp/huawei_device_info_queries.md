# Origem das fixtures de consultas Huawei

Em 26/10/2026, foram executadas duas operações de leitura na CPE Huawei
EG8145X6-10 / V5R022C00S364 da bancada, uma GPN e outra GPV, em sessões distintas.
Ambas terminaram `succeeded`, com uma tentativa e encerramento normal da sessão.

`huawei_device_info_names.xml` e `huawei_device_info_values.xml` são **reconstruções
SOAP anonimizadas dos resultados permitidos persistidos**, não capturas do XML
original no fio. Não preservam prefixos, espaços, ordem original, cabeçalhos
opcionais ou nomes descartados. A validação real exercitou o parser em execução;
essas fixtures exercitam o contrato sem conter material bruto do equipamento.

- GPN: `InternetGatewayDevice.DeviceInfo.`, `NextLevel=true`. A projeção retém
  HardwareVersion, ModelName e SoftwareVersion, todos com `Writable=false`.
  Os demais nomes da descoberta não foram armazenados nem reconstruídos.
- GPV: somente esses três caminhos. Valores confirmados pela resposta real:
  hardware `343D.D`, modelo `EG8145X6-10`, firmware `V5R022C00S364`.
- Os tipos das respostas GPV passaram pela validação de `xsd:string`; os
  identificadores RPC foram substituídos por constantes `ANON-*`.
- Nenhum número de série, endereço, cookie, credencial, nome PPPoE, texto de fault
  ou UUID operacional real faz parte das fixtures.

A evidência vale somente para esse modelo/firmware e esses caminhos. Não declara
suporte a WAN, Wi-Fi, óptica, voz, escrita nem descoberta de toda a árvore.
