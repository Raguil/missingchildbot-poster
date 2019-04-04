# Missing Child Bot - Poster

This is README just temporary nonsense until I implement SES+S3 and a way to read e-mails from
buckets.  There will be a config file containing the bucket name, and then
individual e-mail accounts will be zip codes at raguel.org.  I'll sign up for alerts using addresses
for central municipal buildings (town hall, city hall, etc).  Each zip code will have its own
prefix in the bucket.  Zip codes will be mapped to subreddits using a JSON file that is loaded in
when this script is invoked.  Data about children will be kept in a dictionary that also includes
a key that maps to a set containing all the subreddits that a child will be posted to 
(to prevent duplicates).  All e-mails will be ingested when this script is run, and dictionaries
containing data about children will be generated and stored in another dictionary with the case number
as its key.  Duplicate dictionaries will be avoided by first checking to make sure that the case number 
has not already been encountered.  As a second check,
this script will load in a JSON file with a history of the bot's prior posts.  This JSON file will exist
within the same S3 bucket as the e-mails. If this JSON file does not exist, this script will attempt to 
partially re-create it by looking at the last 100 posts that the bot has made.

The script will then iterate through each of the child data dictionaries in the list and construct a URL
based off the case number and a title based off the child's name.  I prefer using URLs because they 
are taken down automatically off of NCMEC's website, thus making it so that identifiable information
for the children isn't on reddit any longer than it needs to be.  In these cases, more than any other,
preserving privacy is essential.  It will then check the title against the list of its past 100 reddit posts-
if the title is the same, it will skip it.

Since the URL will automatically become stale after a case is resolved, this script will ignore the
"Please take down posters" e-mails that start with 'Missing Child Poster Notification:'.  In a future iteration,
it might be nice to have this scrub names from post titles as it receives these.

When the script is done, it will remove all the e-mails from the bucket that were there when it was invoked.

The script will run as a cron in lambda at some interval (maybe every 15 minutes) with concurrency set so
that only one instance of the script can run at a time.
