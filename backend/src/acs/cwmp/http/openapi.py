"""OpenAPI examples for the development SOAP adapter, not protocol validation."""

from typing import Any

INFORM_EXAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
 xmlns:cwmp="urn:dslforum-org:cwmp-1-0"
 xmlns:enc="http://schemas.xmlsoap.org/soap/encoding/">
 <soap:Header><cwmp:ID soap:mustUnderstand="1">swagger-lab-001</cwmp:ID></soap:Header>
 <soap:Body><cwmp:Inform>
  <DeviceId>
   <Manufacturer>Example</Manufacturer><OUI>A1B2C3</OUI>
   <ProductClass>LAB</ProductClass><SerialNumber>SWAGGER0001</SerialNumber>
  </DeviceId>
  <Event enc:arrayType="cwmp:EventStruct[1]">
   <EventStruct><EventCode>1 BOOT</EventCode><CommandKey/></EventStruct>
  </Event>
  <MaxEnvelopes>1</MaxEnvelopes>
  <CurrentTime>2026-09-19T14:00:00Z</CurrentTime>
  <RetryCount>0</RetryCount>
  <ParameterList enc:arrayType="cwmp:ParameterValueStruct[0]"/>
 </cwmp:Inform></soap:Body>
</soap:Envelope>"""

REQUEST_BODY = {
    "required": False,
    "description": "Inform ou resposta de consulta em SOAP/XML; POST vazio continua a sessão e pode receber um RPC pendente.",
    "content": {
        "text/xml": {
            "schema": {"type": "string"},
            "examples": {
                "inform": {"summary": "Synthetic Inform", "value": INFORM_EXAMPLE},
                "empty": {"summary": "Empty POST (after Inform)", "value": ""},
            },
        }
    },
}

RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"description": "HTTP Basic credentials required (acs-cpe realm)."},
    403: {
        "description": "HTTPS required or credentials do not match the Inform identity."
    },
    200: {
        "description": "InformResponse com MaxEnvelopes=1 e cookie, ou GetParameterNames/GetParameterValues pendente no POST vazio.",
        "content": {"text/xml": {"schema": {"type": "string"}}},
    },
    204: {
        "description": "Session completed; no pending operation and no response body."
    },
    400: {
        "description": "Invalid XML/envelope or missing, expired or invalid session."
    },
    408: {"description": "Request body read timeout."},
    413: {"description": "Body exceeds the 1 MiB limit."},
    415: {"description": "Unsupported content type or content encoding."},
    500: {
        "description": "CWMP 1.0 SOAP Fault: 8000 or 8003.",
        "content": {"text/xml": {"schema": {"type": "string"}}},
    },
    503: {"description": "Service or database unavailable; Inform not acknowledged."},
}
