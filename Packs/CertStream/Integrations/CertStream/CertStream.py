import json
import traceback
from contextlib import contextmanager
from uuid import UUID, uuid5

from websockets.exceptions import InvalidStatus, ConnectionClosed
from websockets.sync.client import connect

from CommonServerPython import *  # noqa: F401

VENDOR = "Kali Dog Security"
PRODUCT = "CertStream"
SCO_DET_ID_NAMESPACE = UUID('00abedb4-aa42-466c-9c01-fed23315a9b7')
FETCH_SLEEP = 5
XSOAR_TIME_FORMAT = '%Y-%m-%dT%H:%M:%S+00:00'


def test_module(host: str):
    # set the fetch interval to 2 seconds so we don't get timeout for the test module
    recv_timeout = 2
    try:
        with websocket_connections(host) as (message_connection):
            json.loads(message_connection.recv(timeout=recv_timeout))
            return "ok"

    except InvalidStatus as e:
        if e.response.status_code == 401:
            return_error("Authentication failed. Please check the Cluster ID and API key.")


@contextmanager
def websocket_connections(host: str):
    demisto.info(f"Starting websocket connection to {host}")
    url = host
    with connect(url) as message_connection:
        yield message_connection


def long_running_execution_command(host: str, fetch_interval: int):
    """ Executes to long running loop and checks if an update to the homographs is needed

    Args:
        host (str): The URL for the websocket connection
        fetch_interval (int): The interval in minutes to check for updates to the homographs list
    """

    while True:
        with websocket_connections(host) as (message_connection):
            demisto.info("Connected to websocket")

            while True:
                now = datetime.now()
                context = json.loads(demisto.getIntegrationContext()["context"])
                last_fetch_time = datetime.strptime(context["fetch_time"], '%Y-%m-%d %H:%M:%S')
                fetch_interval = context["list_update_interval"]

                if now - last_fetch_time >= timedelta(minutes=fetch_interval):
                    context["homographs"] = get_homographs_list(context["word_list_name"])
                    context["fetch_time"] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    demisto.setIntegrationContext({"context": json.dumps(context)})

                try:
                    message = message_connection.recv()
                    fetch_certificates(message)

                except ConnectionClosed:
                    break


def fetch_certificates(message: str):
    """ Fetches the certificates data from the CertStream socket

    Args:
        connection (Connection): The connection to the socket, used to iterate over messages

    """

    message = json.loads(message)
    data = message["data"]
    cert = data["leaf_cert"]
    all_domains = cert["all_domains"]

    if len(all_domains) == 0:
        return  # No domains found in the certificate
    else:
        for domain in all_domains:
            # Check for homographs
            is_suspicious_domain, result = check_homographs(domain)
            if is_suspicious_domain:
                now = datetime.now()
                demisto.info(f"Potential homograph match found for domain: {domain}")
                create_xsoar_certificate_indicator(data)
                create_xsoar_incident(data, domain, now, result)


def build_xsoar_grid(data: dict) -> list:
    return [{"title": key, "data": value or ""} for key, value in data.items()]


def set_incident_severity(similarity: float) -> int:
    """ Returns the Cortex XSOAR incident severity (1-4) based on the homograph similarity score

    Args:
        similarity (float): Similarity score between 0 and 1.

    Returns:
        int: Cortex XSOAR Severity (1 to 4)
    """

    if similarity >= 0.85:
        return IncidentSeverity.CRITICAL

    elif 0.85 > similarity >= 0.75:
        return IncidentSeverity.HIGH

    elif 0.75 > similarity >= 0.65:
        return IncidentSeverity.MEDIUM

    else:
        return IncidentSeverity.LOW


def threat_summit_example():
    data = r"""
    {
  "cert_index": 824806432,
  "cert_link": "https://ct.googleapis.com/testtube/ct/v1/get-entries?start=824806432&end=824806432",
  "leaf_cert": {
    "all_domains": [
      "test.paypa1.xyz"
    ],
    "extensions": {
      "authorityInfoAccess": "CA Issuers - URI:http://stg-e1.i.lencr.org/\nOCSP - URI:http://stg-e1.o.lencr.org\n",
      "authorityKeyIdentifier": "keyid:EB:F9:25:C2:80:28:66:E2:6D:08:92:32:F3:C2:E1:AD:C3:FF:35:45\n",
      "basicConstraints": "CA:FALSE",
      "certificatePolicies": "Policy: 2.23.140.1.2.1",
      "ctlPoisonByte": true,
      "extendedKeyUsage": "TLS Web server authentication, TLS Web client authentication",
      "keyUsage": "Digital Signature",
      "subjectAltName": "DNS:test.paypa1.xyz",
      "subjectKeyIdentifier": "10:26:DE:54:97:48:8D:90:2B:FF:21:2A:C9:75:9A:A2:59:4F:11:95"
    },
    "fingerprint": "78:6B:41:06:89:F9:E1:1F:6A:95:96:54:AB:0B:9F:BF:CB:B4:4F:AA",
    "issuer": {
      "C": "US",
      "CN": "(STAGING) Ersatz Edamame E1",
      "L": null,
      "O": "(STAGING) Let's Encrypt",
      "OU": null,
      "ST": null,
      "aggregated": "/C=US/CN=(STAGING) Ersatz Edamame E1/O=(STAGING) Let's Encrypt",
      "emailAddress": null
    },
    "not_after": 1705307110,
    "not_before": 1697531111,
    "serial_number": "FAFC65D6E0179461AB2A6DBBFBB204DBC426",
    "signature_algorithm": "sha384, ecdsa",
    "subject": {
      "C": null,
      "CN": "test.paypa1.xyz",
      "L": null,
      "O": null,
      "OU": null,
      "ST": null,
      "aggregated": "/CN=test.paypa1.xyz",
      "emailAddress": null
    }
  },
  "seen": 1697534775.374554,
  "source": {
    "name": "Google 'Testtube' log",
    "url": "https://ct.googleapis.com/testtube/"
  },
  "update_type": "PrecertLogEntry"
}
    """

    data = json.loads(data)
    create_xsoar_incident(data, "test.paypa1.xyz", datetime.now(), {"similarity": 0.9, "homograph": "paypa1", "asset": "paypal"})
    create_xsoar_certificate_indicator(data)
    return_error('Done')


def create_xsoar_incident(certificate: dict, domain: str, current_time: datetime, result: dict):
    """Creates an XSOAR 'New Suspicious Domain` incident using the certificate and domain details

    Args:
        certificate (dict): The certificate details from CertStream
        domain (str): The domain matching the homograph
        current_time (datetime): The time the match occurred at
        result (dict): A dictionary containing details about the match like similarity score and matched asset.
    """
    demisto.info(f'Creating a new suspicious domain incident for {domain}')

    incident = {
        "name": f"Suspicious Domain Discovered - {domain}",
        "occured": current_time.strftime('%Y-%m-%d %H:%M:%S'),
        "type": "New Suspicious Domain",
        "severity": set_incident_severity(result["similarity"]),
        "CustomFields": {
            "fingerprint": certificate["leaf_cert"]["fingerprint"],
            "levenshteindistance": result["similarity"],
            "userasset": result["asset"],
            "certificatesource": certificate["source"]["name"],
            "certificateindex": certificate["cert_index"],
            "externallink": certificate["cert_link"]
        }
    }

    demisto.createIncidents([incident])
    demisto.info(f'Done creating new incident for {domain}')


def create_xsoar_certificate_indicator(certificate: dict):
    """Creates an XSOAR certificate indicator

    Args:
        certificate (dict): An X.509 certificate object from CertStream
    """
    certificate_data = certificate["leaf_cert"]

    demisto.info(f'Creating an X.509 indicator {certificate_data["fingerprint"]}')

    demisto.createIndicators([{
        "type": "X509 Certificate",
        "value": certificate_data["fingerprint"],
        "sourcetimestamp": datetime.fromtimestamp(certificate["seen"]).strftime(XSOAR_TIME_FORMAT),
        "fields": {
            "stixid": create_stix_id(certificate_data["serial_number"]),
            "serialnumber": certificate_data["serial_number"],
            "validitynotbefore": datetime.fromtimestamp(certificate_data["not_before"]).strftime(XSOAR_TIME_FORMAT),
            "validitynotafter": datetime.fromtimestamp(certificate_data["not_after"]).strftime(XSOAR_TIME_FORMAT),
            "source": certificate["source"]["name"],
            "domains": [{"domain": domain} for domain in certificate_data["all_domains"]],
            "signaturealgorithm": certificate_data["signature_algorithm"].replace(" ", "").split(","),
            "subject": build_xsoar_grid(certificate_data["subject"]),
            "issuer": build_xsoar_grid(certificate_data["issuer"]),
            "x.509v3extensions": build_xsoar_grid(certificate_data["issuer"]),
            "tags": ["Fake Domain", "CertStream"]
        },
        "rawJSON": certificate,
        "relationships": create_relationship_list(certificate_data["fingerprint"], certificate_data["all_domains"])
    }])


def create_stix_id(serial_number: str) -> str:
    """Generates a STIX ID for the indicator

    Args:
        serial_number (str): The certificate serial number

    Returns:
        str: A STIX ID
    """

    jsonize = json.dumps({"serial_number": serial_number}).replace(" ", "")
    uuid = uuid5(SCO_DET_ID_NAMESPACE, jsonize)
    return f'x509-certificate--{str(uuid)}'


def create_relationship_list(value: str, domains: list[str]) -> list[EntityRelationship]:
    """Creates an XSOAR relationship object

    Args:
        value (str): The certificate fingerprint value
        domains (list[str]): A list of domains in the certificate

    Returns:
        list[EntityRelationship]: A list of XSOAR relationship objects
    """
    relationships = []
    entity_a = value
    for domain in domains:
        relation_obj = EntityRelationship(
            name=EntityRelationship.Relationships.RELATED_TO,
            entity_a=entity_a,
            entity_a_type="X.509 Certificate",
            entity_b=domain,
            entity_b_type="Domain")
        relationships.append(relation_obj.to_indicator())
    return relationships


def check_homographs(domain: str) -> tuple[bool, dict]:
    """Checks each word in a domain for similarity to the provided homograph list.

    Args:
        domain (str): The domain to check for homographs
        user_homographs (dict): A list of homograph strings from XSOAR
        levenshtein_distance_threshold (float): The Levenshtein distance threshold for determining similarity between strings

    Returns:
        bool: Returns True if any word in the domain matches a homograph, False otherwise
    """
    integration_context = json.loads(demisto.getIntegrationContext()["context"])
    user_homographs = integration_context["homographs"]
    levenshtein_distance_threshold = integration_context["levenshtein_distance_threshold"]

    words = domain.split(".")[:-1]  # All words in the domain without the TLD

    for word in words:
        for asset, homographs in user_homographs.items():
            for homograph in homographs:
                similarity = compute_similarity(homograph, word)
                if similarity > levenshtein_distance_threshold:
                    return True, {"similarity": similarity,
                                  "homograph": homograph,
                                  "asset": asset}

    return False, {"similarity": similarity,
                   "homograph": homograph,
                   "asset": asset}


def get_homographs_list(list_name: str) -> dict:
    """Fetches ths latest list of homographs from XSOAR

    Args:
        list_name (str): The name of the XSOAR list to fetch

    Returns:
        list: A list of homographs
    """
    try:
        lists = json.loads(demisto.internalHttpRequest("GET", "/lists/").get("body", {}))
        demisto.info('Fetching homographs list from XSOAR ({word_list_name})')

    except Exception as e:
        demisto.error(f"{e}")

    for user_list in lists:
        if user_list["id"] != list_name:
            continue

        else:
            return json.loads(user_list["data"])

    demisto.error("List of words not found")
    raise OSError


def levenshtein_distance(original_string: str, reference_string: str) -> float:
    """The Levenshtein distance is a string metric for measuring the difference between two sequences.

    Args:
        original_string (str): The initial string to compare to
        reference_string (str): The string to compare against the original

    Returns:
        float: The Levenshtein distance between the two strings
    """
    m, n = len(original_string), len(reference_string)
    if m < n:
        original_string, reference_string = reference_string, original_string
        m, n = n, m
    d = [list(range(n + 1))] + [[i] + [0] * n for i in range(1, m + 1)]
    for j in range(1, n + 1):
        for i in range(1, m + 1):
            if original_string[i - 1] == reference_string[j - 1]:
                d[i][j] = d[i - 1][j - 1]
            else:
                d[i][j] = min(d[i - 1][j], d[i][j - 1], d[i - 1][j - 1]) + 1
    return d[m][n]


def compute_similarity(input_string: str, reference_string: str) -> float:
    """Computes the Levenshtein similarity between two strings.

    Args:
        input_string (str): The initial string to compare
        reference_string (str): The new string to compare against the input string

    Returns:
        float: _description_
    """
    distance = levenshtein_distance(input_string, reference_string)
    max_length = max(len(input_string), len(reference_string))
    similarity = 1 - (distance / max_length)
    return similarity


def main():  # pragma: no cover
    command = demisto.command()
    params = demisto.params()
    host: str = params["url"]
    word_list_name: str = params["list_name"]
    list_update_interval: int = params.get("update_interval", 30)
    levenshtein_distance_threshold: float = float(params.get("levenshtein_distance_threshold", 0.85))
    logging.getLogger('websockets.client').setLevel(logging.ERROR)

    demisto.setIntegrationContext({"context": json.dumps({
        "word_list_name": word_list_name,
        "list_update_interval": list_update_interval,
        "levenshtein_distance_threshold": levenshtein_distance_threshold,
        "homographs": get_homographs_list(word_list_name),
        "fetch_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }, default=str)})

    try:
        if command == "long-running-execution":
            return_results(long_running_execution_command(host, list_update_interval))
        elif command == "test-module":
            return_results(test_module(host))
        elif command == "threat-summit":
            return_results(threat_summit_example())
        else:
            raise NotImplementedError(f"Command {command} is not implemented.")
    except Exception:
        return_error(f"Failed to execute {command} command.\nError:\n{traceback.format_exc()}")


if __name__ in ("__main__", "__builtin__", "builtins"):
    main()
