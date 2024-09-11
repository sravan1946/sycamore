import datetime
from html.parser import HTMLParser
import json
import os
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

import boto3
import marko
from marko.md_renderer import MarkdownRenderer
import requests
from openai import OpenAI
from openai.types.chat import ChatCompletionToolParam
import streamlit as st
from streamlit_pdf_viewer import pdf_viewer
import sycamore
from sycamore.query.client import SycamoreQueryClient
from sycamore.query.logical_plan import LogicalPlan
from util import generate_plan, run_plan

NUM_DOCS_GENERATE = 60
NUM_DOCS_PREVIEW = 10
NUM_TEXT_CHARS_GENERATE = 2500

# Set OpenAI API key from Streamlit secrets
openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

OPENSEARCH_INDEX = "const_ntsb"
OS_CONFIG = {"search_pipeline": "hybrid_pipeline"}
OS_CLIENT_ARGS = {
    "hosts": [{"host": "localhost", "port": 9200}],
    "http_compress": True,
    "http_auth": ("admin", "admin"),
    "use_ssl": True,
    "verify_certs": False,
    "ssl_assert_hostname": False,
    "ssl_show_warn": False,
    "timeout": 120,
}

EXAMPLE_QUERIES = [
    "How many incidents were there in Washington in 2023?",
    "What was the breakdown of aircraft types for incidents with substantial damage?",
    "Show me incidents involving Piper aircraft",
    "Show the details on accident ERA23LA153",
]

WELCOME_MESSAGE = f"""Welcome to the NTSB incident query demo! You can ask me questions about NTSB
incident reports, and I'll do my best to answer them. Feel free to ask about specific incidents,o
aggregate statistics, or anything else you're curious about.
If you're not sure what to ask, you can try one of the following example queries:

{"".join([f"<SuggestedQuery query='{query}' />" for query in EXAMPLE_QUERIES])}
"""

SYSTEM_PROMPT = """You are a helpful agent that answers questions about NTSB
(National Transportation Safety Board) incidents. You have access to a database of incident
reports, each of which has an associated PDF document, as well as metadata about the incident
including the location, date, aircraft type, and more. You can answer questions about the
contents of individual reports, as well as aggregate statistics about the incidents in the
database. You can perform actions such as filtering, sorting, and aggregating the data to
answer questions. You can also provide links to relevant documents and data sources.

All your responses should be in MDX, which is Markdown augmented with JSX components. 
As an example, your response could include a table of data, a link to a document, or a
component such as a button. Below is an example of MDX output that you might generate:

```Here are the latest incidents referring to bad weather:

<Table>
    <TableHeader>
        <TableCell>Incident ID</TableCell>
        <TableCell>Date</TableCell>
        <TableCell>Location</TableCell>
        <TableCell>Aircraft Type</TableCell>
    </TableHeader>
    <TableRow>
        <TableCell>ERA23LA153</TableCell>
        <TableCell>2023-01-15</TableCell>
        <TableCell>Seattle, WA</TableCell>
        <TableCell>Cessna 172</TableCell>
    </TableRow>
</Table>
```

Additional markdown text can follow the use of a JSX component, and JSX components can be
inlined in the markdown text as follows:

```For more information about this incident, please see the following document:
   <Preview path="s3://aryn-public/samples/sampledata1.pdf" />.
```

Do not include a starting ``` and closing ``` line in your reply. Just respond with the MDX itself.
Do not include extra whitespace that is not needed for the markdown interpretation. For instance,
if a JSX component has a property that's a JSON object, encode it into a single line, like so:

<Component prop="{\"key1\": \"value1\", \"key2": \"value2\"}" />

The following JSX components are available for your use:
  * <Table>
      <TableHeader>
        <TableCell>Column name 1</TableCell>
        <TableCell>Column name 2</TableCell>
      </TableHeader>
      <TableRow>
        <TableCell>Value 1</TableCell>
        <TableCell>Value 2</TableCell>
      </TableRow>
      <TableRow>
        <TableCell>Value 3</TableCell>
        <TableCell>Value 4</TableCell>
      </TableRow>
    </Table>
    Displays a table showing the provided data. 
 
  * <Preview path="s3://aryn-public/samples/sampledata1.pdf" />
    Displays an inline preview of the provided document. You may provide an S3 path or a URL.

  * <SuggestedQuery query="How many incidents were there in Washington in 2023?" />
    Displays a button showing a query that the user might wish to consider asking next.

  * <Map>
     <MapMarker lat="47.6062" lon="-122.3321" />
     <MapMarker lat="47.7105" lon="-122.4406" />
    </Map>
    Displays a map with markers at the provided coordinates.

Multiple JSX components can be used in your reply. You may ONLY use these specific JSX components
in your responses. Other than these components, you may ONLY use standard Markdown syntax.

Please use <Preview> any time a specific document or incident is mentioned, and <Map> any time
there is an opportunity to refer to a location.

Please suggest 1-3 follow-on queries (using the <SuggestedQuery> component) that the user might
ask, based on the response to the user's question.
"""


class MDXParser(HTMLParser):

    def __init__(self, chat_message: "ChatMessage"):
        super().__init__()
        self.chat_message = chat_message
        self.in_table = False
        self.in_header = False
        self.in_row = False
        self.in_cell = False
        self.table_data: List[List[Any]] = []
        self.row_data: List[Any] = []
        self.header_data: List[Any] = []
        self.in_map = False
        self.map_data: List[Tuple[float, float]] = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.in_table = True
            self.table_data = []
        elif tag == "tableheader" and self.in_table:
            self.in_header = True
            self.header_data = []
        elif tag == "tablerow" and self.in_table:
            self.in_row = True
            self.row_data = []
        elif tag == "tablecell" and self.in_row:
            self.in_cell = True
        elif tag == "map":
            self.in_map = True
            self.map_data = []
        elif tag == "mapmarker" and self.in_map:
            lat = float(dict(attrs).get("lat"))
            lon = float(dict(attrs).get("lon"))
            self.map_data.append((lat, lon))

    def handle_endtag(self, tag):
        if tag == "tablecell" and self.in_cell:
            self.in_cell = False
        elif tag == "tableheader" and self.in_header:
            self.in_header = False
            self.table_data.append(self.header_data)
        elif tag == "tablerow" and self.in_row:
            self.in_row = False
            self.table_data.append(self.row_data)
        elif tag == "table" and self.in_table:
            self.in_table = False
            self.chat_message.render_table(self.table_data)
        elif tag == "map" and self.in_map:
            self.in_map = False
            self.chat_message.render_map(self.map_data)

    def handle_startendtag(self, tag, attrs):
        if tag == "preview":
            path = dict(attrs).get("path")
            if path:
                self.chat_message.render_preview(path)
            else:
                self.chat_message.render_markdown("No path provided for preview.")
        elif tag == "suggestedquery":
            query = dict(attrs).get("query")
            if query:
                self.chat_message.render_suggested_query(query)
            else:
                self.chat_message.render_markdown("No query provided for suggested query")
        elif tag == "mapmarker" and self.in_map:
            lat = float(dict(attrs).get("lat"))
            lon = float(dict(attrs).get("lon"))
            self.map_data.append((lat, lon))

    def handle_data(self, data):
        data = data.strip()
        if not data:
            return
        if self.in_header:
            self.header_data.append(data)
        elif self.in_cell:
            self.row_data.append(data)
        else:
            self.chat_message.render_markdown(data)


class JSXElementParser(HTMLParser):
    tag = None
    props = None

    def handle_startendtag(self, tag, attrs):
        self.tag = tag
        self.props = dict(attrs)


class MDRenderer(MarkdownRenderer):
    """A Marko renderer that replaces S3 links with links to the demo instance."""

    def __init__(self):
        super().__init__()

    def render_link(self, element: marko.inline.Link) -> str:
        if element.dest.startswith("s3://"):
            # Replace S3 links with a link to the demo instance.
            # (This is somewhat of a temporary hack until we build out better UI for this.)
            element.dest = element.dest.replace("s3://", "https://luna-demo.dev.aryn.ai/doc/")
        return super().render_link(element)


class ChatMessageExtra:
    def __init__(self, name: str, content: Any):
        self.name = name
        self.content = content


class ChatMessage:
    def __init__(self, message: Optional[Dict[str, Any]] = None, extras: Optional[List[ChatMessageExtra]] = None):
        self.message_id = st.session_state.next_message_id
        st.session_state.next_message_id += 1
        self.timestamp = datetime.datetime.now()
        self.message = message or {}
        self.extras = extras or []
        self.widget_key = 0

    def show(self):
        self.widget_key = 0
        with st.chat_message(self.message.get("role", "assistant")):
            self.show_content()

    def show_content(self):
        for extra in self.extras:
            with st.expander(extra.name):
                st.write(extra.content)
        if self.message.get("content"):
            content = self.message.get("content")
            self.render_markdown_with_jsx(content)

    def button(self, label: str, **kwargs):
        return st.button(label, key=self.next_key(), **kwargs)

    def download_button(self, label: str, content: bytes, filename: str, file_type: str, **kwargs):
        return st.download_button(label, content, filename, file_type, key=self.next_key(), **kwargs)

    def next_key(self) -> str:
        key = f"{self.message_id}-{self.widget_key}"
        self.widget_key += 1
        return key

    def render_markdown_with_jsx(self, text: str):
        print(text)
        mdxparser = MDXParser(self)
        mdxparser.feed(text)

    def render_markdown(self, text: str):
        st.markdown(text)

    def render_table(self, data: List[Dict[str, Any]]):
        st.dataframe(data)

    def render_preview(self, path: str):
        Preview(path, self).show()

    def render_suggested_query(self, query: str):
        if self.button(query):
            st.session_state.user_query = query

    def render_map(self, data: List[Tuple[float, float]]):
        df = pd.DataFrame(data, columns=["lat", "lon"])
        st.map(df)

    def to_dict(self) -> Optional[Dict[str, Any]]:
        return self.message

    def __str__(self):
        return f"{self.message.get('role', 'assistant')}: {self.message.get('content')}"


st.title("Sycamore NTSB Query Demo")

TOOLS: List[ChatCompletionToolParam] = [
    {
        "type": "function",
        "function": {
            "name": "queryDataSource",
            "description": """Run a query against the backend data source. The query should be a natural language 
            query and can represent operations such as filters, aggregations, sorting, formatting,
            and more. This function should only be called when new data is needed; if the data
            is already available in the message history, it should be used directly.""",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The user's query",
                    },
                },
                "required": ["query"],
            },
        },
    },
]


def parse_s3_path(s3_path: str) -> Tuple[str, str]:
    """Parse an S3 path into a bucket and key."""
    s3_path = s3_path.replace("s3://", "")
    bucket, key = s3_path.split("/", 1)
    return bucket, key


class Preview:
    def __init__(self, path: str, chat_message: ChatMessage):
        self.path = path
        self.chat_message = chat_message

    def show(self):
        if self.path.startswith("s3://"):
            bucket, key = parse_s3_path(self.path)
            s3 = boto3.client("s3")
            response = s3.get_object(Bucket=bucket, Key=key)
            content = response["Body"].read()
        elif self.path.startswith("http"):
            content = requests.get(self.path, timeout=30).content
        else:
            st.write(f"Unknown path format: {self.path}")
            return

        if st.session_state.get("pagenum") is None:
            st.session_state.pagenum = 1

        with st.container(border=True):
            st.write(f"`{self.path}`")
            tab1, tab2 = st.tabs(["PDF", "Metadata"])
            with tab1:
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    if self.chat_message.button("First", use_container_width=True):
                        st.session_state.pagenum = 1
                with col2:
                    if self.chat_message.button("Prev", use_container_width=True):
                        st.session_state.pagenum = max(1, st.session_state.pagenum - 1)
                with col3:
                    if self.chat_message.button("Next", use_container_width=True):
                        st.session_state.pagenum += 1
                with col4:
                    self.chat_message.download_button(
                        "Download", content, os.path.basename(self.path), "pdf", use_container_width=True
                    )
                pdf_viewer(content, pages_to_render=[st.session_state.pagenum], key=self.chat_message.next_key())


# TODO: Fetch underlying Sycamore doc and show metadata here.
#            with tab2:
#                props = {k: v for k, v in doc.properties["entity"].items() if v is not None}
#                st.dataframe(props)


def query_data_source(query: str, index: str) -> Tuple[Any, LogicalPlan]:
    """Run a Sycamore query."""
    sqclient = SycamoreQueryClient(
        s3_cache_path=st.session_state.s3_cache_path if st.session_state.use_cache else None,
    )
    with st.spinner("Generating plan..."):
        plan = generate_plan(sqclient, query, index)
        with st.expander("Query plan"):
            st.write(plan)
    with st.spinner("Running query plan..."):
        st.session_state.query_id, result = run_plan(sqclient, plan)
    return result, plan


def docset_to_string(docset: sycamore.docset.DocSet) -> str:
    BASE_PROPS = [
        "filename",
        "filetype",
        "page_number",
        "page_numbers",
        "links",
        "element_id",
        "parent_id",
        "_schema",
        "_schema_class",
        "entity",
    ]
    retval = ""
    for doc in docset.take(NUM_DOCS_GENERATE):
        if isinstance(doc, sycamore.data.MetadataDocument):
            continue
        props_dict = doc.properties.get("entity", {})
        props_dict.update({p: doc.properties[p] for p in set(doc.properties) - set(BASE_PROPS)})
        props_dict["text_representation"] = (
            doc.text_representation[:NUM_TEXT_CHARS_GENERATE] if doc.text_representation is not None else None
        )
        retval += json.dumps(props_dict, indent=2) + "\n"
    return retval


def show_messages():
    for msg in st.session_state.messages:
        if msg.message.get("role") not in ["user", "assistant"]:
            continue
        if msg.message.get("content") is None:
            continue
        msg.show()


def do_query():
    prompt = st.session_state.user_query
    st.session_state.user_query = None
    user_message = ChatMessage({"role": "user", "content": prompt})
    st.session_state.messages.append(user_message)
    user_message.show()

    assistant_message = None
    query_plan = None
    with st.chat_message("assistant"):
        # We loop here because tool calls require re-invoking the LLM.
        while True:
            messages = [{"role": "system", "content": SYSTEM_PROMPT}] + [m.to_dict() for m in st.session_state.messages]
            with st.spinner("Running query..."):
                response = openai_client.chat.completions.create(
                    model=st.session_state["openai_model"],
                    messages=messages,
                    tools=TOOLS,
                    tool_choice="auto",
                )
            response_dict = response.choices[0].message.to_dict()
            assistant_message = ChatMessage(response_dict)
            st.session_state.messages.append(assistant_message)

            tool_calls = response.choices[0].message.tool_calls
            if tool_calls:
                tool_call_id = tool_calls[0].id
                tool_function_name = tool_calls[0].function.name
                tool_args = json.loads(tool_calls[0].function.arguments)
                if tool_function_name == "queryDataSource":
                    tool_query = tool_args["query"]
                    tool_response, query_plan = query_data_source(tool_query, OPENSEARCH_INDEX)
                else:
                    tool_response = f"Unknown tool: {tool_function_name}"

                with st.expander("Raw query response"):
                    st.write(tool_response)

                if isinstance(tool_response, str):
                    # We got a straight string response from the query plan, which means we can
                    # feed it back to the LLM directly.
                    tool_response_str = tool_response
                elif isinstance(tool_response, sycamore.docset.DocSet):
                    # We got a DocSet.
                    tool_response_str = docset_to_string(tool_response)
                else:
                    # Fall back to string representation.
                    tool_response_str = str(tool_response)

                with st.expander("Tool response"):
                    st.write(tool_response_str)

                tool_response_message = ChatMessage(
                    {
                        "role": "tool",
                        "content": tool_response_str,
                        "tool_call_id": tool_call_id,
                        "name": tool_function_name,
                    }
                )
                st.session_state.messages.append(tool_response_message)

            else:
                # No function call was made.
                assistant_message.show_content()
                # Stash away the query plan in the message in case we want to show it later.
                if query_plan:
                    assistant_message.extras.append(ChatMessageExtra("Query plan", query_plan))
                break


def main():
    # Set a default model
    if "openai_model" not in st.session_state:
        st.session_state["openai_model"] = "gpt-4o"

    if "s3_cache_path" not in st.session_state:
        st.session_state.s3_cache_path = "s3://aryn-temp/llm_cache/luna/query-demo"

    if "use_cache" not in st.session_state:
        st.session_state.use_cache = True

    if "next_message_id" not in st.session_state:
        st.session_state.next_message_id = 0

    if "user_query" not in st.session_state:
        st.session_state.user_query = None

    if "messages" not in st.session_state:
        st.session_state.messages = [ChatMessage({"role": "assistant", "content": WELCOME_MESSAGE})]

    show_messages()

    if prompt := st.chat_input("Ask me anything"):
        st.session_state.user_query = prompt

    while st.session_state.user_query is not None:
        do_query()


if __name__ == "__main__":
    main()