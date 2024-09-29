from dotenv import load_dotenv
import chainlit as cl
import json
import random
from prompts import SYSTEM_PROMPT_TOOLS, RAG_PROMPT, tools
from movie_functions import get_now_playing_movies, get_showtimes, get_reviews, buy_ticket

load_dotenv()

# Note: If switching to LangSmith, uncomment the following, and replace @observe with @traceable
# from langsmith.wrappers import wrap_openai
# from langsmith import traceable
# client = wrap_openai(openai.AsyncClient())

from langfuse.decorators import observe
from langfuse.openai import AsyncOpenAI
 
client = AsyncOpenAI()

gen_kwargs = {
    "model": "gpt-4o",
    "temperature": 0.2,
    "max_tokens": 500
}

@observe
@cl.on_chat_start
def on_chat_start():    
    message_history = [{"role": "system", "content": SYSTEM_PROMPT_TOOLS}]
    cl.user_session.set("message_history", message_history)

@observe
async def generate_response(client, message_history, gen_kwargs):
    response_message = cl.Message(content="")
    await response_message.send()

    stream = await client.chat.completions.create(
        messages=message_history, 
        tools=tools,
        tool_choice="auto",
        stream=True, 
        **gen_kwargs)
    
    full_response = ""
    is_tool_call = False
    tool_calls = []
    current_tool_call_index = None
    current_tool_call = None
    
    async for part in stream:
        delta = part.choices[0].delta
        
        if delta.content:
            await response_message.stream_token(delta.content)
            full_response += delta.content

        if delta.tool_calls:
            is_tool_call = True
            # print("Tool calls: " + str(delta.tool_calls))
            for tool_call in delta.tool_calls:
                if tool_call.index is not current_tool_call_index:  # A new tool call is starting
                    if current_tool_call:
                        tool_calls.append(current_tool_call)
                    current_tool_call = {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments or ""
                    }
                    current_tool_call_index = tool_call.index
                else:
                    if tool_call.function and tool_call.function.arguments:
                        current_tool_call["arguments"] += tool_call.function.arguments
                    elif tool_call.function and tool_call.function.name:
                        current_tool_call["names"] += tool_call.function.name

    if current_tool_call:
        tool_calls.append(current_tool_call)

    print("Tool calls: " + str(tool_calls))

    await response_message.update()

    return response_message, full_response, message_history, is_tool_call, tool_calls

@observe
async def check_for_reviews(client, message_history, gen_kwargs):
    updated_message_history = message_history.copy()
    if updated_message_history and updated_message_history[0]["role"] == "system":
        # Replace the existing system message
        updated_message_history[0] = {"role": "system", "content": RAG_PROMPT}
    else:
        # Insert a new system message at the beginning
        updated_message_history.insert(0, {"role": "system", "content": RAG_PROMPT})

    response = await client.chat.completions.create(messages=updated_message_history, **gen_kwargs)
    try:
        response_json = json.loads(response.choices[0].message.content)
        print("Checking for reviews: " + str(response_json))
        if response_json.get("fetch_reviews", False):
            movie_id = response_json.get("id")
            reviews = get_reviews(movie_id)
            reviews_string = f"Reviews for {response_json.get('movie')} (ID: {movie_id}):\n\n{reviews}"
            context_message = {"role": "system", "content": f"CONTEXT: {reviews_string}"}
            message_history.append(context_message)
            return message_history
    except json.JSONDecodeError:
        pass

    return message_history


@cl.on_message
@observe
async def on_message(message: cl.Message):
    message_history = cl.user_session.get("message_history", [])
    message_history.append({"role": "user", "content": message.content})
    message_history = await check_for_reviews(client, message_history, gen_kwargs)
    
    while True:
        response_message, full_response, updated_message_history, is_tool_call, tool_call_data = await generate_response(client, message_history, gen_kwargs)

        print("Is tool call: " + str(is_tool_call))
        print("Tool call data: " + str(tool_call_data))
        
        if not is_tool_call:
            break
        
        for tool_call in tool_call_data:
            print("Tool call: " + str(tool_call))            
            function_name = tool_call["name"]
            function_args = json.loads(tool_call["arguments"])
            
            if function_name is None:
                continue
            
            print("Function name: " + function_name)
            print("Function args: " + str(function_args))
            
            
            if function_name == "get_now_playing_movies":
                result = get_now_playing_movies()
            elif function_name == "get_showtimes":
                result = get_showtimes(**function_args)
            elif function_name == "get_reviews":
                result = get_reviews(**function_args)
            elif function_name == "buy_ticket":
                result = await buy_ticket(**function_args)
            else:
                result = "Unknown function"

            system_message = {
                "role": "system",
                "content": f"Function '{function_name}' was called with arguments {function_args}. The result is:\n{result}"
            }
            updated_message_history.append(system_message)
        
        # If the function was get_now_playing_movies and we need to pick a random movie
        if function_name == "get_now_playing_movies" and "random" in message.content.lower():
            random_item = random.randint(1, 5)
            print("Random item:" + str(random_item))
            system_message = {
                "role": "system",
                "content": f"User asked for a random movie, so we chose movie at position {random_item}. If the total list of movies is less than {random_item}, chose the last movie in the list."
            }
            updated_message_history.append(system_message)
        
        message_history = updated_message_history
    
    message_history.append({"role": "assistant", "content": response_message.content})
    cl.user_session.set("message_history", message_history)



if __name__ == "__main__":
    cl.main()
