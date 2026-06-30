import json
from typing import Any, Dict

import requests
from langflow.custom import Component
from langflow.io import DataInput, MessageTextInput, Output
from langflow.schema import Data


class Plant360AIOutputComponent(Component):
    display_name = "Plant360 AI Output Component"
    description = "Extract flow_id from webhook trigger and send final data to API endpoint"
    documentation: str = "https://docs.langflow.org/components-custom-components"
    icon = "code"
    name = "plant360-output-trigger"

    inputs = [
        DataInput(
            name="webhook_trigger",
            display_name="Webhook Trigger",
            info="Webhook Trigger which sends flow id from backend",
            is_list=False,
            required=True,
        ),
        DataInput(
            name="final_data",
            display_name="Final Data",
            info="The final data to be sent in the API request body",
            is_list=False,
            required=True,
        ),
        MessageTextInput(
            name="base_url",
            display_name="Base URL",
            info="Base URL for the Langflow Backend",
            value="http://localhost:7860",
            required=True,
        ),
    ]

    outputs = [
        Output(display_name="Response", name="response", method="build_output"),
    ]

    def extract_flow_id(self, webhook_data: Any) -> str:
        try:
            if isinstance(webhook_data, Data):
                data = webhook_data.data if hasattr(webhook_data, "data") else webhook_data.value
            else:
                data = webhook_data

            if isinstance(data, dict):
                if "flow_id" in data:
                    return str(data["flow_id"])
                for key in ["flowId", "flow-id", "id"]:
                    if key in data:
                        return str(data[key])

            elif isinstance(data, str):
                try:
                    parsed_data = json.loads(data)
                    if isinstance(parsed_data, dict) and "flow_id" in parsed_data:
                        return str(parsed_data["flow_id"])
                except json.JSONDecodeError:
                    pass

            raise ValueError("flow_id not found in webhook trigger data")

        except Exception as e:
            self.status = f"Error extracting flow_id: {str(e)}"
            raise e

    def prepare_request_data(self, final_data: Any) -> Dict[str, Any]:
        try:
            if isinstance(final_data, Data):
                data = final_data.data if hasattr(final_data, "data") else final_data.value
            else:
                data = final_data

            if isinstance(data, dict):
                return data
            if isinstance(data, list):
                return {"isolation_points": data}
            if isinstance(data, str):
                try:
                    parsed = json.loads(data)
                    if isinstance(parsed, list):
                        return {"isolation_points": parsed}
                    if isinstance(parsed, dict):
                        return parsed
                    return {"data": parsed}
                except json.JSONDecodeError:
                    return {"data": data}

            return {"data": data}

        except Exception as e:
            self.status = f"Error preparing request data: {str(e)}"
            return {"data": str(final_data)}

    def build_output(self) -> Data:
        try:
            flow_id = self.extract_flow_id(self.webhook_trigger)
            request_body = self.prepare_request_data(self.final_data)
            api_endpoint = f"{self.base_url.rstrip('/')}/api/v1/flows/end_flow/{flow_id}"

            response = requests.post(
                api_endpoint,
                json=request_body,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=30,
            )
            response.raise_for_status()

            response_data = {
                "status": "success",
                "flow_id": flow_id,
                "api_endpoint": api_endpoint,
                "status_code": response.status_code,
                "response_data": response.json() if response.content else None,
                "sent_data": request_body,
            }

            self.status = f"Successfully sent data to flow {flow_id}"
            return Data(value=response_data)

        except requests.RequestException as e:
            error_data = {
                "status": "error",
                "error_type": "request_error",
                "error_message": str(e),
                "flow_id": flow_id if "flow_id" in locals() else None,
                "api_endpoint": api_endpoint if "api_endpoint" in locals() else None,
            }
            self.status = f"Request failed: {str(e)}"
            return Data(value=error_data)

        except Exception as e:
            error_data = {
                "status": "error",
                "error_type": "general_error",
                "error_message": str(e),
            }
            self.status = f"Error: {str(e)}"
            return Data(value=error_data)
