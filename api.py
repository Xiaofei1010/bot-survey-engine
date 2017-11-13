from flask import Flask
from flask_restful import reqparse, Resource, Api, request
import json
import os
import sys
import logging
import sqlalchemy
import pymysql
import time
pymysql.install_as_MySQLdb()
import atexit
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from gahelper.gahelper import Gahelper
from gahelper import gaformatter
from GA_project import ga_all

sys.path.insert(0, './survey')
import survey
from survey import Survey

sys.path.insert(0, './episodes')
import recommendation
from recommendation import episode

sys.path.insert(0, './episodes/word_vec_bigram')
# import load_file_from_bucket
from load_file_from_bucket import load_word_vec

sys.path.insert(0, './listener_reminder')
import listener_reminder
from listener_reminder import Listener_Reminder


logname = sys.argv[0]
logger = logging.getLogger(logname)
formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
logger.setLevel(logging.INFO)

logfile = '/var/tmp/' + logname + '.log'
ldir = os.path.dirname(logfile)
if not(os.path.isdir(ldir)):
    os.makedirs(ldir)
hdlr = logging.FileHandler(logfile)

hdlr.setFormatter(formatter)
logger.addHandler(hdlr) 
stdout = logging.StreamHandler()
stdout.setFormatter(formatter)
logger.addHandler(stdout)

version = "0.0.1"

#survey
print('*************survey session*************')
with open ("./config/config.json", "r") as myfile:
        data = json.load(myfile)
        #mysql
        username = data['mysql']['username']
        address = data['mysql']['address']
        password = data['mysql']['password']
        databasename = data['mysql']['databasename']
        #gensim model
        size = data['model_paras']['size']
        min_count = data['model_paras']['min_count']
        window = data['model_paras']['window']
        name = str(size) + "_" + str(window) + "_"+ str(min_count) 
        # aws
        user = data['aws']['accessKeyId']
        pw = data['aws']['secretAccessKey']
        url = "mysql://%s:%s@%s/%s" % (username, password, address,databasename)
survey_instance = Survey(username, password, address, databasename)

class GetQuestion(Resource):
    def get(self, question_id):
        question_text = survey_instance.get_question_text(question_id)
        resp = {
            "question_id": question_id,
            "question_text": question_text
        }
        return resp

class SaveAnswer(Resource):
    #def post(self, response_id = None, question_id, question_order):
    def post(self):
        r = request.get_data()
        req = json.loads(r.decode('utf-8'))

        answer_text = req['answer_text']
        question_id = req['question_id']
        question_order = req['question_order']
        if 'response_id' in req:
            response_id = req['response_id']
            print('response_id is ', response_id)
        else:
            response_id = None
            print('response_id is none.')
        
        magic_text = survey_instance.get_magic_reply(answer_text, question_id)
        next_question_id = survey_instance.get_next_question_id(question_id, answer_text)
        response_id, response_answer_id = survey_instance.save_answer(response_id, question_id, question_order, answer_text)
        
        resp = { "magic_text": magic_text, "response_id": response_id, "next_question_id": int(next_question_id)}
        if next_question_id == -1:
            content = survey_instance.survey_retrieval(next_question_id, response_id)
            print(content)
            if content.empty:
                print("No data for the current survey.")
            else:
                survey_instance.send_email(content, user, pw)
        return resp

# episode
start = time.time()
print("*************episode session*************")
print('Downloading word_vec from AWS S3...')
load_word_vec_instance = load_word_vec()
update_episode = False
episode_instance = episode(update_episode,username, address,password,databasename)
class give_recommendation(Resource):
    def post(self):
        r = request.get_data()
        req = json.loads(r.decode('utf-8'))
        user_request = req['request']
        start = time.time()
        result = episode_instance.recommend_episode(user_request)
        print("the time it takes to make a recommendation is ", time.time() - start)
        # start = time.time()
        # episode_instance.save_recommendation_table(user_request, result)
        # print('the time it takes to save the recommendation to table is ', time.time() - start)
        if len(result) > 0:
            return result
        else:
            return None

print("How long does it spend in the episode session ", time.time() - start)

class save_recommendation(Resource):
    def post(self):
        r = request.get_data()
        info = json.loads(r.decode('utf-8'))
        print('info is ', info)
        user_request = info.get('user_request')
        recommendation = info.get('recommendation')
        start = time.time()
        episode_instance.save_recommendation_table(user_request,recommendation)
        print('the time it takes to save the recommendation to table is ', time.time() - start)

#listener_reminder
print("*************listener reminder session*************")
reminder_ins = Listener_Reminder(user, pw, username, password, address, databasename)

class reminder(Resource):
    def post(self):
        r = request.get_data() # request is RAW body in REST Console.
        user_info = json.loads(r.decode('utf-8'))
        print('user_info is ', user_info)
        contact_type = user_info.get('contact_type')
        contact_account = user_info.get('contact_account')
        #reminder_time = user_info.get('reminder_time') 
        reminder_time = user_info.get('reminder_time')
        episode_title = user_info.get('episode_title')
        episode_link = user_info.get('episode_link')
        # save reminder task into the table.
        reminder_ins.save_reminder_task(contact_type, contact_account,reminder_time, 
                                                episode_title, episode_link)

        return " Reminder will be sent."# + str(alarm_time)

#GA 
print("********************* Google Analytics ***********************")
ga_instance = ga_all.ga(update_model = False)

class google_analytics(Resource):
    def post(self):
        r = request.get_data()
        user_info = json.loads(r.decode('utf-8'))
        print('user_info is ', user_info)
        user_request = user_info.get('user_request')
        f = ga_instance.run(user_request)
        return f # f is in json form: for example {'img': 'http://dataskeptic-static.s3.amazonaws.com/bot/ga-images/2017-11-10/transactions_userType_2016-11-10_2017-11-10.png', 'txt': ''}

if __name__ == '__main__':
    logger.info("Init")
    app = Flask(__name__)
    api = Api(app)
    parser = reqparse.RequestParser()
    # survey
    api.add_resource(GetQuestion,  '/survey/question/<int:question_id>')
    api.add_resource(SaveAnswer,   '/survey/response/answer/save')
    # episode
    api.add_resource(give_recommendation,   '/episode/recommendation')
    api.add_resource(save_recommendation,   '/episode/save_recommendation')
    # listener_reminder
    api.add_resource(reminder,  '/listener_reminder')
    
    api.add_resource(google_analytics, '/ga') # {'user_request':...} return f see above, f is in json form
    
    @app.before_first_request 
    def add_tasks():
        #scheduler = BlockingScheduler()
        scheduler = BackgroundScheduler()
        scheduler.add_job(reminder_ins.checkForReminders, 'interval', seconds=30)
        print('Press Ctrl+{0} to exit scheduler'.format('Break' if os.name == 'nt' else 'C'))
        try:
            scheduler.start()

        except (KeyboardInterrupt, SystemExit):
            pass
        scheduler.print_jobs()
        atexit.register(lambda: scheduler.shutdown())

    app.run(host='0.0.0.0', debug=False, port=3500)

    logger.info("Ready")
    
