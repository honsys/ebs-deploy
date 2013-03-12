from boto.exception import S3ResponseError
from boto.s3.connection import S3Connection
from boto.beanstalk import connect_to_region
from boto.s3.key import Key

from time import time, sleep
import zipfile
import os
import sys
import yaml


class AwsCredentials:
    """
    Class for holding AwsCredentials
    """
    def __init__(self, access_key, secret_key, region, bucket, bucket_path):
        self.access_key = access_key
        self.secret_key = secret_key
        self.bucket = bucket
        self.region = region
        self.bucket_path = bucket_path
        if not self.bucket_path.endswith('/'):
            self.bucket_path = self.bucket_path+'/'

def get(vals, key, default_val=None):
    """
    Returns a dictionary value
    """
    val = vals
    for part in key.split('.'):
        if isinstance(val, dict):
            val = val.get(part, None)
            if val is None:
                return default_val
        else:
            return default_val
    return val

class EbsHelper(object):
    """
    Class for helping with ebs
    """

    def __init__(self, aws, app_name=None):
        """
        Creates the EbsHelper
        """
        self.aws            = aws
        self.ebs            = connect_to_region(aws.region, aws_access_key_id=aws.access_key, aws_secret_access_key=aws.secret_key)
        self.s3             = S3Connection(aws.access_key, aws.secret_key, host='s3-'+aws.region+'.amazonaws.com')
        self.app_name       = app_name


    def create_archive(self, directory, filename, config={}, ignore_predicate=None, ignored_files=['.git', '.svn']):
        """
        Creates an archive from a directory and returns
        the file that was created.
        """
        zip = zipfile.ZipFile(filename, 'w', compression=zipfile.ZIP_DEFLATED)
        root_len = len(os.path.abspath(directory))

        # create it
        print("Creating archive: "+filename)
        for root, dirs, files in os.walk(directory, followlinks=True):
            archive_root = os.path.abspath(root)[root_len+1:]
            for f in files:
                fullpath = os.path.join(root, f)
                archive_name = os.path.join(archive_root, f)

                # ignore the file we're createing
                if filename in fullpath:
                    continue

                # ignored files
                if ignored_files is not None:
                    for name in ignored_files:
                        if fullpath.endswith(name):
                            print("Skipping: "+name)
                            continue

                # do predicate
                if ignore_predicate is not None:
                    if not ignore_predicate(archive_name):
                        print("Skipping: "+archive_name)
                        continue

                print("Adding: "+archive_name)
                zip.write(fullpath, archive_name, zipfile.ZIP_DEFLATED)

        # add config
        for conf in config:
            for conf, tree in conf.items():
                content = None
                if tree.has_key('yaml'):
                    content = yaml.dump(tree['yaml'], default_flow_style=False)
                else:
                    content = tree.get('content', '')
                print("Writing config file for "+str(conf))
                zip.writestr(conf, content, zipfile.ZIP_DEFLATED)

        zip.close()
        return filename


    def upload_archive(self, filename, key, auto_create_bucket=True):
        """
        Uploads an application archive version to s3
        """
        bucket = None
        try:
            bucket = self.s3.get_bucket(self.aws.bucket)
            if bucket.get_location() != self.aws.region:
                raise Exception("Existing bucket doesn't match region")
        except S3ResponseError:
            bucket = self.s3.create_bucket(self.aws.bucket, location=self.aws.region)

        def __report_upload_progress(sent, total):
            if not sent:
                sent = 0
            if not total:
                total = 0
            print("Uploaded "+str(sent)+" bytes of "+str(total) \
                +" ("+str( int(float(max(1, sent))/float(total)*100) )+"%)")

        # upload the new version
        k = Key(bucket)
        k.key = self.aws.bucket_path+key
        k.set_metadata('time', str(time()))
        k.set_contents_from_filename(filename, cb=__report_upload_progress, num_cb=10)

    def list_available_solution_stacks(self):
        """
        Returns a list of available solution stacks
        """
        stacks = self.ebs.list_available_solution_stacks()
        return stacks['ListAvailableSolutionStacksResponse']['ListAvailableSolutionStacksResult']['SolutionStacks']


    def create_application(self, description=None):
        """
        Creats an application and sets the helpers current
        app_name to the created application
        """
        print("Creating application "+self.app_name)
        self.ebs.create_application(self.app_name, description=description)


    def delete_application(self):
        """
        Creats an application and sets the helpers current
        app_name to the created application
        """
        print("Deleting application "+self.app_name)
        self.ebs.delete_application(self.app_name, terminate_env_by_force=True)


    def application_exists(self):
        """
        Returns whether or not the given app_name exists
        """
        response = self.ebs.describe_applications(application_names=[self.app_name])
        return len(response['DescribeApplicationsResponse']['DescribeApplicationsResult']['Applications']) > 0


    def create_environment(self, env_name, version_label=None,
        solution_stack_name=None, cname_prefix=None, description=None,
        option_settings=None):
        """
        Creates a new environment
        """
        print("Creating environment: "+env_name)
        self.ebs.create_environment(self.app_name, env_name,
            version_label=version_label,
            solution_stack_name=solution_stack_name,
            cname_prefix=cname_prefix,
            description=description,
            option_settings=option_settings)

    def environment_exists(self, env_name):
        """
        Returns whether or not the given environment exists
        """
        response = self.ebs.describe_environments(application_name=self.app_name, environment_names=[env_name], include_deleted=False)
        return len(response['DescribeEnvironmentsResponse']['DescribeEnvironmentsResult']['Environments']) > 0 \
            and response['DescribeEnvironmentsResponse']['DescribeEnvironmentsResult']['Environments'][0]['Status'] != 'Terminated'

    def rebuild_environment(self, env_name):
        """
        Rebuilds an environment
        """
        print("Rebuilding "+env_name)
        self.ebs.rebuild_environment(environment_name=env_name)

    def get_environments(self):
        """
        Returns the environments
        """
        response = self.ebs.describe_environments(application_name=self.app_name, include_deleted=False)
        return response['DescribeEnvironmentsResponse']['DescribeEnvironmentsResult']['Environments']

    def delete_environment(self, environment_name):
        """
        Deletes an environment
        """
        self.ebs.terminate_environment(environment_name=environment_name, terminate_resources=True)

    def update_environment(self, environment_name, description=None, option_settings=[]):
        """
        Updates an application version
        """
        print("Updating environment: "+environment_name)
        messages = self.ebs.validate_configuration_settings(self.app_name, option_settings, environment_name=environment_name)
        messages = messages['ValidateConfigurationSettingsResponse']['ValidateConfigurationSettingsResult']['Messages']
        ok = True
        for message in messages:
            if message['Severity'] == 'error':
                ok = False
            print("["+message['Severity']+"] "+environment_name+" - '"+message['Namespace']+":"+message['OptionName']+"': "+message['Message'])
        self.ebs.update_environment(environment_name=environment_name, description=description, option_settings=option_settings)

    def deploy_version(self, environment_name, version_label):
        """
        Deploys a version to an environment
        """
        print("Deploying "+version_label+" to "+environment_name)
        self.ebs.update_environment(environment_name=environment_name, version_label=version_label)

    def create_application_version(self, version_label, key):
        """
        Creates an application version
        """
        print("Creating application version "+version_label+" for "+key)
        self.ebs.create_application_version(self.app_name, version_label, s3_bucket=self.aws.bucket, s3_key=self.aws.bucket_path+key)

    def delete_unused_versions(self, versions_to_keep=10):
        """
        Deletes unused versions
        """

        # get versions in use
        environments = self.ebs.describe_environments(application_name=self.app_name, include_deleted=False)
        environments = environments['DescribeEnvironmentsResponse']['DescribeEnvironmentsResult']['Environments']
        versions_in_use = []
        for env in environments:
            versions_in_use.append(env['VersionLabel'])

        # get all versions
        versions = self.ebs.describe_application_versions(application_name=self.app_name)
        versions = versions['DescribeApplicationVersionsResponse']['DescribeApplicationVersionsResult']['ApplicationVersions']
        versions = sorted(versions, reverse=True, cmp=lambda x, y: cmp(x['DateCreated'], y['DateCreated']))

        # delete versions in use
        for version in versions[versions_to_keep:]:
            if version['VersionLabel'] in versions_in_use:
                print("Not deleting "+version["VersionLabel"]+" because it is in use")
            else:
                print("Deleting unused version: "+version["VersionLabel"])
                self.ebs.delete_application_version(application_name=self.app_name, version_label=version['VersionLabel'])
                sleep(2)


    def wait_for_environments(self, environment_names, health=None, status=None, version_label=None, include_deleted=True, wait_time_secs=600):
        """
        Waits for an environment to have the given version_label
        and to be in the green state
        """

        # turn into a list
        if not isinstance(environment_names, (list, tuple)):
            environment_names = [environment_names]
        environment_names = environment_names[:]

        # print some stuff
        s = "Waiting for environemnt(s) "+(", ".join(environment_names))+" to"
        if health is not None:
            s = s +" have health "+health
        else:
            s = s +" have any health"
        if version_label is not None:
            s = s + " and have version "+version_label
        if status is not None:
            s = s + " and have status "+status
        print(s)

        started = time()
        while True:
            # bail if they're all good
            if len(environment_names)==0:
                break

            # wait
            sleep(5)

            ## get the env
            environments = self.ebs.describe_environments(
                application_name=self.app_name, environment_names=environment_names, include_deleted=include_deleted)
            environments = environments['DescribeEnvironmentsResponse']['DescribeEnvironmentsResult']['Environments']
            if len(environments)<=0:
                raise Exception("Couldn't find any environments")

            # loop through and wait
            for env in environments[:]:
                heathy = env['Health'] == health
                env_name = env['EnvironmentName']

                # the message
                msg = "Environment "+env_name+" is "+env['Health']
                if version_label is not None:
                    msg = msg + " and has version "+env['VersionLabel']
                if status is not None:
                    msg = msg + " and has status "+env['Status']

                # what we're doing
                good_to_go = True
                if health is not None:
                    good_to_go = good_to_go and env['Health'] == health
                if status is not None:
                    good_to_go = good_to_go and env['Status'] == status
                if version_label is not None:
                    good_to_go = good_to_go and env['VersionLabel'] == version_label

                # log it
                if good_to_go:
                    print(msg+" ... done")
                    environment_names.remove(env_name)
                else:
                    print(msg+" ... waiting")

            # check th etime
            elapsed = time()-started
            if elapsed > wait_time_secs:
                raise Exception("Wait time for environemnt(s) "+(" and ".join(environment_names))+" to be "+health+" expired")
