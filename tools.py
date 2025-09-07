from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
import os
import requests
import json

search_endpoint = os.getenv("ai_search_url")
search_key = os.getenv("ai_search_key")
index_name = os.getenv("ai_index_name")
semantic_config = os.getenv("ai_semantic_config")
logic_app_url_shipment_orders = os.getenv("logic_app_url_shipment_orders")
logic_app_url_call_log_analysis = os.getenv("logic_app_url_call_log_analysis")
ecom_api_url = os.getenv("ecom_api_url")

tools_list = [
    {
        "type": "function",
        "name": "perform_search_based_qna",
        "description": "call this function to respond to the user query on Contoso retail policies, procedures and general QnA",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "type": "function",
        "name": "create_delivery_order",
        "description": "call this function to create a delivery order based on order id and destination location",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "destination": {"type": "string"},
            },
            "required": ["order_id", "destination"],
        },
    },
    {
        "type": "function",
        "name": "perform_call_log_analysis",
        "description": "call this function to analyze call log based on input call log conversation text",
        "parameters": {
            "type": "object",
            "properties": {
                "call_log": {"type": "string"},
            },
            "required": ["call_log"],
        },
    },
    {
        "type": "function",
        "name": "search_products_by_category",
        "description": "call this function to search for products by category",
        "parameters": {
            "type": "object",
            "properties": {
                "category": {"type": "string"},
            },
            "required": ["category"],
        },
    },
        {
        "type": "function",
        "name": "order_products",
        "description": "call this function to order products by product id and quantity",
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string"},
                "quantity": {"type": "integer"},
            },
            "required": ["product_id", "quantity"],
        },
    }
]


def perform_search_based_qna(query):
    print("calling search to get context for the response ....")
    credential = AzureKeyCredential(search_key)
    client = SearchClient(
        endpoint=search_endpoint,
        index_name=index_name,
        credential=credential,
    )
    response = client.search(
        search_text=query,
        query_type="semantic",
        semantic_configuration_name=semantic_config,
    )
    response_docs = ""
    counter = 0
    results = list(response)
    for result in results:
        print("--------result------>", result)
        print(
            f"search result from document:{result['metadata_storage_name']}, and content: {result['content']}  "
        )
        response_docs += (
            " --- Document context start ---"
            + result["content"]
            + "\n ---End of Document ---\n"
        )
        counter += 1
        if counter == 2:
            break
    print("***********  calling LLM now ....***************")
    return response_docs


def create_delivery_order(order_id: str, destination: str) -> str:
    """
    creates a consignment delivery order (i.e. a shipment order) for the given order_id and destination location

    :param order_id (str): The order number of the purchase made by the user.
    :param destination (str): The location where the order is to be delivered.
    :return: generated delivery order number.
    :rtype: Any
    """

    api_url = logic_app_url_shipment_orders
    print("creating shipment order using Logic app.................")
    # make a HTTP POST API call with json payload
    response = requests.post(
        api_url,
        json={"order_id": order_id, "destination": destination},
        headers={"Content-Type": "application/json"},
    )

    print("response from shipment order creation", response.text)
    return json.dumps(response.text)


def perform_call_log_analysis(call_log: str) -> str:
    """
    performs call log analysis for the given call log

    :param call_log (str): The call log to be analyzed (JSON string).
    :return: generated response on call analysis.
    :rtype: Any
    """

    api_url = logic_app_url_call_log_analysis
    print("analyzing call log using Logic app.................")
    print(f"DEBUG: Received call_log parameter: {call_log}")
    print(f"DEBUG: call_log type: {type(call_log)}")
    print(f"DEBUG: api_url: {api_url}")
    
    # Parse the call_log as JSON before sending to Logic App
    try:
        print("DEBUG: Attempting to parse call_log as JSON...")
        call_log_json = json.loads(call_log)
        print(f"DEBUG: Successfully parsed JSON: {call_log_json}")
    except json.JSONDecodeError as e:
        print(f"Error parsing call_log as JSON: {e}")
        print(f"DEBUG: Failed to parse. Raw call_log: {repr(call_log)}")
        return json.dumps({"error": "Invalid JSON format in call_log"})
    
    # make a HTTP POST API call with json payload
    try:
        print("DEBUG: Making POST request to Logic App...")
        response = requests.post(
            api_url,
            json={"call_logs": call_log_json},
            headers={"Content-Type": "application/json"},
        )
        print(f"DEBUG: Response status code: {response.status_code}")
        print("response from call log analysis", response.text)
        return json.dumps(response.text)
    except Exception as e:
        print(f"ERROR: Exception during API call: {e}")
        return json.dumps({"error": f"API call failed: {str(e)}"})


available_functions = {
    "perform_search_based_qna": perform_search_based_qna,
    "create_delivery_order": create_delivery_order,
    "perform_call_log_analysis": perform_call_log_analysis,
    "search_products_by_category": lambda category: requests.get(
        f"{ecom_api_url}/SearchProductsByCategory?category={category}"
    ).json(),
    "order_products": lambda product_id, quantity: requests.get(
        f"{ecom_api_url}/orderproduct?id={product_id}&quantity={quantity}"
    ).json(),
}
