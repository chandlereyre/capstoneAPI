import redis
from flask import Flask, request, session, send_file
from PIL import Image, ImageOps
import base64
import io
from flask_cors import CORS
from flask_session import Session
from pymongo import MongoClient
from flask_socketio import SocketIO, join_room, leave_room, send

# app config
app = Flask(__name__)
CORS(app, origins="*", supports_credentials=True)
app.config['IMG_FOLDER'] = 'img'
app.config['SECRET_KEY'] = "secret key"
app.config['SESSION_TYPE'] = 'redis'
app.config['SESSION_PERMANENT'] = True
app.config['SESSION_USE_SIGNER'] = True
app.config['SESSION_REDIS'] = redis.from_url('redis://localhost:6379')
app.config['SESSION_COOKIE_SAMESITE'] = "None"
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_NAME'] = "clumpusapi.duckdns.org"
app.config['CORS_HEADERS'] = 'Content-Type'


# mongo init
db = MongoClient("localhost", 27017).chatApp

# Create Flask-Session
Session(app)

# Create socketIO
socketio = SocketIO(app, cors_allowed_origins="*", manage_session=False)

@app.route("/auth/login", methods = ['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.json['username']
        password = request.json['password']

        # check if username & password combination exists in mongoDB
        if db.user.find_one({'username': username, 'password': password}):
            session['username'] = username
            return 'session created'
        
        elif db.user.find_one({'username': username}):
            return 'incorrect password'
        
        else:
            return 'user not found'
        
    if request.method == 'GET':
        data = {}

        if 'username' in session:
            data = {
                "loggedIn": True,
                "username": session.get('username', None)
            }
            return data
        
        else:
            data = {
                "loggedIn": False,
                "username": None
            }
            return data
        
    return ''

@app.route("/auth/logout", methods = ['DELETE'])
def logout():
    session.pop('username')
    return 'session deleted'

@app.route("/check-user", methods = ['POST'])
def checkUser():
    if db.user.find_one({'username': request.json['username']}):
        return "user found"
    
    else:
        return "user not found"
    
@app.route("/create-account", methods = ['POST'])
def createAccount():
    username = request.json['username']
    password = request.json['password']

    if db.user.find_one({'username': username}):
        return "user already exists"
    
    else:
        db.user.insert_one({'username': username, 'password': password, 'bio': "", 'chats': [], 'profilePic': "/img/defaultProfilePic.png"})
        return 'account created'
    
@app.route("/get-chats", methods = ['GET'])
def getChats():
    username = session.get('username', None)
    data = {}
    chats = db.user.find_one({'username': username})["chats"]

    for room in chats:
        lastMessage = ""
        profilePic = ""

        if db.chats.find_one({'room': room}):
            roomOBJ = db.chats.find_one({'room': room})

            if len(roomOBJ['messages']) > 0:
                lastMessage = roomOBJ['messages'][-1]['message']

            # for 2 users
            if len(roomOBJ['users']) == 2:
                otherUser = ""

                if roomOBJ['users'][0] == username:
                    otherUser = roomOBJ['users'][1]

                else:
                    otherUser = roomOBJ['users'][0]
                profilePic = "http://3.15.224.228" + db.user.find_one({'username': otherUser})['profilePic']
                data[room] = {'lastMessage': lastMessage, 'profilePic': profilePic, 'with': [otherUser], 'group': False}
            
            if len(roomOBJ['users']) > 2:
                profilePic = "http://3.15.224.228" + "/img/defaultProfilePic.png" # do something better here later
                otherUsers = roomOBJ['users']
                otherUsers.remove(username)
                data[room] = {'lastMessage': lastMessage, 'profilePic': profilePic, 'with': otherUsers, 'group': True}
    
    return data

@app.route("/create-chat", methods = ['POST'])
def createChat():
    recipientsArr = request.json['recipients']
    username = session.get('username', None)
    recipientsArr.append(username)
    room = computeRoom(recipientsArr)

    if db.chats.find_one({'room': room}):
        return "Chat already exists"

    for user in recipientsArr:
        if db.user.find_one({'username': user}):
            chats = db.user.find_one({'username': user})["chats"]
            chats.append(room)
            db.user.update_one({'username': user}, {"$set": {"chats": chats}})

    db.chats.insert_one({'room': room, 'messages': [], 'users': recipientsArr, 'profilePic': ""})
    return "Chat created"
    
@app.route("/get-messages", methods = ['POST'])
def getMessages():
    room = computeRoom(request.json['users'])
    page = request.json['page']

    print(page)

    if db.chats.find_one({'room': room}):
        messagesArr = db.chats.find_one({'room': room})['messages']
        length = len(messagesArr)

        startIndex = length - page * 20 - 20
        endIndex = length - page * 20

        if startIndex < 0:
            print("length out of bounds")
            messages = messagesArr[0:endIndex]
            print("messages length: " + str(len(messages)))

        else:
            messages = messagesArr[startIndex:endIndex]
            print("messages length: " + str(len(messages)))

        return {"messages": messages, "messageCount": len(messagesArr)}
    
    else:
        return {"messages": [], "messageCount": 0}

@app.route("/get-profile-pic", methods=['POST'])
def getProfilePic():
    if db.user.find_one({'username': request.json['username']}):
        profilePic = db.user.find_one({'username': request.json['username']})['profilePic']

        if profilePic != "":
            return "http://3.15.224.228" + profilePic
        
        else:
            return "http://3.15.224.228" + "/img/defaultProfilePic.png"
        
    else:
        return "user not found"
    
@app.route("/get-profile-info", methods=['POST'])
def getProfileInfo():
    user = request.json['username']

    if db.user.find_one({'username': user}):
        userData = db.user.find_one({'username': user})
        responseData = {
            "username": userData['username'],
            "bio": userData['bio'],
            "profilePic": userData['profilePic']
        }
        return responseData
    
    else:
        return "user not found"

@app.route("/update-profile", methods=['POST'])
def updateProfile():
    user = session.get('username', None)
    bio = request.json['bio']

    if (request.json['profilePic'][0:5] == 'data:'):
        # convert dataURL to image
        head, image = request.json['profilePic'].split(',', 1)

        bits = head.split(';')
        mime_type = bits[0] if bits[0] else 'text/plain'    
        _, file_type = mime_type.split('/')
        
        b = base64.b64decode(image)

        img = Image.open(io.BytesIO(b))

        # save image locally in 'img' folder
        img.save(f'./img/{user}.{file_type}', quality=50)
    
        if db.user.find_one({'username': user}):
            db.user.update_one({'username': user}, {'$set': {'bio': bio, 'profilePic': f'/img/{user}.{file_type}'}})
            
            return "user updated"
        
    if db.user.find_one({'username': user}):
        db.user.update_one({'username': user}, {'$set': {'bio': bio}})
        return "user updated"
    
    else:
        return "user not found"
    
# IMAGE HOSTING
@app.route('/img/<imagename>', methods=["GET"])
def getImage(imagename):
    _, file_type = imagename.split('.')
    return send_file(f"./img/{imagename}", mimetype=f"image/{file_type}")

# SOCKET IO / WEBSOCKET
@socketio.on('joinWithUsers')
def join_chat_with_users(data):
    room = computeRoom(data['users'])
    join_room(room)

@socketio.on('joinWithRoom')
def join_chat_with_room(data):
    join_room(data['room'])

@socketio.on('leave')
def leave_chat(data):
    room = computeRoom(data['users'])
    leave_room(room)

@socketio.on('leaveWithRoom')
def leave_with_room(data):
    leave_room(data['room'])

@socketio.on('chatMessage')
def handle_message(data):
    message = data['message']
    sender = data['sender']
    recipients = data['recipients']
    room = computeRoom([sender] + recipients)

    if db.chats.find_one({'room': room}):
        messages = db.chats.find_one({'room': room})['messages']
        messages.append({'message': message, 'from': sender})
        db.chats.update_one({'room': room}, {'$set': {'messages': messages}})

    socketio.emit('chatMessage', {'message': message, 'from': sender, 'room': room}, room=room)

def computeRoom(users):
    users.sort()
    room = ""

    for user in users:
        room += user

    return room

if __name__ == '__main__':  
    socketio.run(app, allow_unsafe_werkzeug=True)
