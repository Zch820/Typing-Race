from starlette.responses import HTMLResponse
from starlette.templating import Jinja2Templates
from fastapi import FastAPI, Request
import redis.asyncio as redis_async
from urllib.parse import parse_qs
import uvicorn
import socketio
import json
import random
import os

app = FastAPI()
sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")
socket_app = socketio.ASGIApp(sio, other_asgi_app=app)
r = redis_async.Redis(host='localhost', port=6379, db=0)
templates = Jinja2Templates(directory=os.path.dirname(__file__))
TEXTS = [
    "The cat is sleeping on the warm sunny window",
    "I drink cold water after running in the park",
    "She reads a funny book while eating cake with friends",
    "We watch stars at night and talk about our happy dreams"
]

# -------------------- TEMPLATE ------------------------
@app.get('/', response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# ---------------------- RUN ---------------------------

if __name__ == "__main__":
    uvicorn.run("main:socket_app", host="127.0.0.1", port=5050, reload=True)

# --------------------- EVENTS -------------------------

user_dict = {}
round_counter = 0
done_players = 0
random_text = None
has_locked = False
has_host = None
user_id = None



@sio.event
async def connect(sid, environ):
    print(f"Client {sid} connected")
    global round_counter, random_text, has_host

    query = parse_qs(environ.get("QUERY_STRING", ""))
    user_id = query.get("user_id", ["anonymous"])[0]
    user_dict[sid] = user_id
    print(f"User connected: {user_id}")

    if not has_host:
        has_host = user_id

    await sio.emit("you_are_host", {"is_host": (user_id == has_host)}, to=sid)


@sio.event
async def restart(sid):
    global round_counter, random_text, done_players, has_locked
    has_locked = False
    round_counter += 1
    done_players = 0
    random_text = random.choice(TEXTS)
    await sio.emit("text", random_text)


@sio.on('typed_char')
async def results(sid, character):
    char = character.get('char')
    idx = character.get('index')

    global random_text
    user_id = user_dict.get(sid, 'anonymous')

    if char == random_text[idx]:
        await r.hset(f'{user_id}:round_{round_counter}', str(idx), '1')
    else:
        await r.hset(f'{user_id}:round_{round_counter}', str(idx), '0')


@sio.on('finished_typing')
async def handle_finished_typing(sid):
    global has_locked
    if not has_locked:
        has_locked = True
        await sio.emit("lock_typing")
    else:
        print(f"{sid} tried to finish after lock")


@sio.on('finish')
async def finish(sid):
    print(f"Client {sid} finished")
    await calculate_results(sid)


async def display_results():
    global round_counter, random_text
    winner_id = await r.get(f'round_{round_counter}_winner')
    winner_score = await r.get(f'round_{round_counter}_winner_score')
    rounds = {
        'round_count': round_counter,
        'text': random_text,
        'winner': winner_id.decode(),
        'winner_score': winner_score.decode(),
    }
    await sio.emit('rounds', rounds)


@sio.on('calculate_results')
async def calculate_results(sid):
    global random_text, round_counter, done_players
    user_id = user_dict.get(sid, 'anonymous')
    client_result_values = await r.hvals(f'{user_id}:round_{round_counter}')
    correct_char_number = 0
    incorrect_char_number = 0

    for value in client_result_values:
        if value == b'1':
            correct_char_number += 1
        elif value == b'0':
            incorrect_char_number += 1
        else:
            pass

    scores_json = {
        'correct': correct_char_number,
        'incorrect': incorrect_char_number,
    }


    await r.hset(f'scores:round_{round_counter}', user_id, json.dumps(scores_json))
    scores_data = await r.hgetall(f'scores:round_{round_counter}')


    scores_decoded_data = {key.decode(): json.loads(value.decode()) for key, value in scores_data.items()}
    winner_id, winner_scores = max(scores_decoded_data.items(),
                                  key=lambda item: item[1]['correct'] - item[1]['incorrect'])
    winner_score = winner_scores['correct'] - winner_scores['incorrect']

    await r.set(f'round_{round_counter}_winner', winner_id)
    await r.set(f'round_{round_counter}_winner_score', winner_score)

    done_players += 1
    if done_players >= len(user_dict):
        await display_results()


@sio.event
async def disconnect(sid):
    print(f"Client {sid} disconnected")
    global round_counter, has_host
    user_id = user_dict.pop(sid, None)

    if has_host not in user_dict:
        round_counter = 0

        if not user_dict:
            has_host = None
            await r.flushdb()
            return

        if user_id == has_host:
            first_sid = next(iter(user_dict))
            has_host = user_dict[first_sid]
            await sio.emit("you_are_host", {"is_host": True}, to=first_sid)





