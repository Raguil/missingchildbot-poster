#!/usr/bin/env python
import os, sys
import gzip
import configparser
import json
import boto3
import email
import praw
import requests
from time import sleep
from bs4 import BeautifulSoup

POSTERBASE='http://www.missingkids.com/poster/NCMC'
LOCKFILE='missingchildbot.lock'
LOCATIONS='locations.json'
STATEFILE='state.json.gz'

'''
DONE:
* Give SES access to S3 bucket
* Generate credentials so that this script can access the S3 bucket.
* Created S3 buckets (missingchildbot and missingchildbot-test).
* Verified raguel.org domain w/ SES.
* Prevented concurrency issues with a lockfile.

TODO:
* Generate JSON file of locations
* Save out results and check against them on subsequent runs.
* Add logging
'''

def getURL(posterInfo):
	'''
	In most cases, the URL for the child's poster is going to be http://www.missingkids.com/poster/NCMC/[CASE NUMBER]/1.
	In some cases, however, the URL has a different digit at the end, notably when multiple children have been
	abducted within the same case.  To take care of this edge case, we'll have requests send a get request
	for the URL and then match what's in the response against the child's name.
	'''
	titleFiller = 'Have you seen this child?'
	# Seems unlikely that there would be more than 10 people missing for a single case.
	for idx in range(1, 11):
		try:
			url = '/'.join([POSTERBASE, posterInfo['Case Number'], str(idx)])
			r = requests.get(url)
			soup = BeautifulSoup(r.content,'html.parser') 
			name = soup.find_all('title')
			if name:
				name = name[0].string.replace(titleFiller,'').strip()
			if name == posterInfo['Name'].upper().strip():
				return url
		except:
			# Something went wrong; URL couldn't be determined, so don't post.
			return None
	# If it finds no page, return None.
	return None

def main():
	# Only run if no other instances of this script are running.
	if LOCKFILE in os.listdir():
		sys.exit(1)
	else:
		with open(LOCKFILE,'w') as f:
			f.write('\n')

	# Read in mapping from zip code to subreddit
	with open(LOCATIONS,'r') as f:
		locations = json.load(f)

	# Read in state from previous runs.
	if os.path.exists(STATEFILE):
		with gzip.open(STATEFILE, 'r') as f:
			state = json.load(f)
	else:
		state = {}

	# Read in the credentials. 
	config = configparser.ConfigParser()
	config.read('config.ini')

	# Create a session object to read in all e-mails.
	session = boto3.Session(aws_access_key_id=config['aws']['aws_access_key_id'],aws_secret_access_key=config['aws']['aws_secret_access_key'])
	s3 = session.resource('s3')
	bucket = s3.Bucket(config['aws']['bucket'])

	# Read in all messages and put poster info in a dictionary, with case numbers for keys.
	allPosterInfo = {}
	for obj in bucket.objects.all():
		### DEBUG
		print("OBJECT: %s" % str(obj.key))
		object = s3.Object(config['aws']['bucket'],obj.key)
		fetchedObject = object.get()
		msg = email.message_from_bytes(fetchedObject['Body'].read())

		posterInfo = {}
		# Message is an initial alert with attached poster
		if msg.is_multipart() and 'Missing Child Alert in your Area:' in msg['Subject']:
			for submsg in msg.walk():
				# We've found the table with relevant info to extract.
				if submsg.get_content_type() == 'text/html':
					tmpKey = ''
					soup = BeautifulSoup(submsg.get_payload(decode=True), 'html.parser')
					for table in soup.find_all('tr'):
						for cell in table.find_all('td'):
							if not cell.string:
								continue
							elif ':' in cell.string:
								tmpKey = cell.string.strip(':')
							elif tmpKey:
								posterInfo[tmpKey] = cell.string.strip('\n')
		# Leave new signups alone.
		elif 'ADAM Program Verification' in msg['Subject'] or 'ADAM Program Conformation' in msg['Subject']:
			continue
		
		### DEBUG
		print(posterInfo)

		# FOR FUTURE REFERENCE
		# Message is a poster recall announcement with no poster
		#if not msg.is_multipart() and 'Missing Child Poster Notification:' in msg['Subject']:

		# This is not an alert to put up a missing child poster.
		# Delete the object and ignore.
		if not posterInfo:
			#object.delete()
			continue

		# Only add to the list of posters to put up if we haven't seen this case before.
		# Also, add to a set of subreddits this poster will be sent to.
		if '/' in obj.key:
			zipCode = obj.key.split('/')[0].strip()
		else:
			# Can't determine where to post, continue
			continue
		if zipCode in locations:
			subreddits = set(locations[zipCode]['subreddits'])
			area = locations[zipCode]['area']
		else:
			# Can't determine where to post, continue
			continue

		### Debug
		print(zipCode)
		print(subreddits)

		if posterInfo['Case Number'] not in allPosterInfo:
			if 'subreddits' not in posterInfo:
				posterInfo['subreddits'] = subreddits
			if 'areas' not in posterInfo:
				posterInfo['areas'] = [area]
			allPosterInfo[posterInfo['Case Number']] = posterInfo
		else:
			# This shouldn't happen but we'll build in some logic to handle this just in case.
			if 'subreddits' not in allPosterInfo[posterInfo['Case Number']]:
				allPosterInfo[posterInfo['Case Number']]['subreddits'] = subreddits
			else: 
				allPosterInfo[posterInfo['Case Number']]['subreddits'] = subreddits.union(allPosterInfo[posterInfo['Case Number']]['subreddits'])
			# This shouldn't happen but we'll build in some logic to handle this just in case.
			if 'areas' not in allPosterInfo[posterInfo['Case Number']]:
				allPosterInfo[posterInfo['Case Number']]['areas'] = [area]
			else: 
				allPosterInfo[posterInfo['Case Number']]['areas'].append(area)

		#Delete the message from the bucket here.
		#object.delete()

	print(allPosterInfo)

	for posterInfo in allPosterInfo.values():
		# Track whether or not to add an entirely new case to the state file.
		addState = False
		
		# Cast the subreddits back to a list so that they can be dumped to JSON later on.
		posterInfo['subreddits'] = list(posterInfo['subreddits'])

		# Post the initial alert to reddit within the proper regional subreddits.
		reddit = praw.Reddit(client_id=config['missingkidbot']['client_id'], client_secret=config['missingkidbot']['client_secret'], password=config['missingkidbot']['password'], user_agent=config['missingkidbot']['user_agent'], username=config['missingkidbot']['username'])

		# Replace "area" with an actual list of all the areas that correspond to zip codes that matched this alert.
		title = "Missing Child Alert in %s: %s" % (', '.join(posterInfo['areas']), posterInfo['Name'])
		url = getURL(posterInfo)

		# Only post if there is a valid URL.
		if url:
			if posterInfo['Case Number'] not in state.keys():
				# This is a totally new case not kept track of in the state file.
				addState = True
				print("%s is new" % posterInfo['Name'])
			for subreddit in posterInfo['subreddits']:
				# Check if we've already posted to this subreddit.
				if not addState:
					if subreddit in state[posterInfo['Case Number']]['subreddits']:
						print("%s is old, no repost" % posterInfo['Name'])
						# Already posted to this subreddit.  Refrain from reposting.
						continue
				### DEBUG
				print("ACTUAL SUBREDDIT: %s" % subreddit)
				#continue
				# For testing
				#subreddit = 'reddit_api_test'
				# Subscribe to the subreddit if you're not already in it.
				if subreddit not in list(reddit.user.subreddits(limit=None)):
					reddit.subreddit(subreddit).subscribe()
				# Try posting 5 times before giving up.
				for tries in range(5):
					try:
						reddit.subreddit(subreddit).submit(title, url=url)
						'''
						If the above line executes without raising an exception
						we have a success and should update the state if this is 
						a subreddit we haven't posted to before.
						Since the conditional above has us skip reposts, we should
						be good to do this.  Unless we're waiting to add the entire case.
						'''
						if not addState:
							state[posterInfo['Case Number']]['subreddits'].append(subreddit)
						break
					except:
						# Try waiting a little over 10 minutes
						sleep(10.25*60)
		if addState:	
			state[posterInfo['Case Number']] = posterInfo

	# Save out current state of postings.
	with gzip.open(STATEFILE, 'wt') as f:
    		json.dump(state, f)
	
	# Allow other instatiations of this script to start.
	os.remove(LOCKFILE)	

if __name__ == '__main__':
	main()
