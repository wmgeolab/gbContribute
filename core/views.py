from django.http import HttpResponse
from django.shortcuts import redirect
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from decouple import config

import os
import subprocess
import json
import logging
import zipfile
import tempfile
import shapefile
import shutil
from github import Github
from datetime import datetime
from urllib.request import urlopen
from urllib.parse import quote_plus
import geopandas as gpd

LOGBASEPATH = "/home/rohith/work/gbcontributebackend/logfile.log"

# logging.config.dictConfig(settings.LOGGING)
# logger = logging.getLogger(__name__)

def pLogger(type, message, path=LOGBASEPATH):
    with open(path, "a") as f:
        f.write(str(datetime.now().strftime('%Y-%m-%d %H:%M:%S')) + ": (" + str(type) + ") " + str(message) + "\n")

def get_timehash():
    from hashlib import blake2b
    import time
    k = str(time.time()).encode('utf-8')
    h = blake2b(key=k, digest_size=8)
    return h.hexdigest()

@csrf_exempt
def api_poke(request):
    '''Poke the heroku server to wake it up from its slumber (free account).'''
    return HttpResponse(status=204)
    

@csrf_exempt
def api_contribute(request):
    '''Receives form data from gbContribute.html, standardizes and outputs this information
    as a zip archive according to the geoBoundaries contribution guidelines, adds the zip archive 
    to a forked branch under 'geoBoundaryBot/geoBoundaries/sourceData', and submits this as a PR to 
    'wmgeolab/geoBoundaries'.
    '''
    print('received')
    data = request.POST
    print(data)
    print(request.FILES)

    # create meta.txt expected by gb PR
    meta_file = create_meta_file(data)
    print('meta', meta_file)

    # standardize the given zip/shapefile to a new shapefile
    fileobj = request.FILES['file']
    file_size = fileobj.size
    print("File size", fileobj, file_size)
    archive = zipfile.ZipFile(fileobj)
    print("zipped the obj data")
    for name in archive.namelist():
        if data['path'].endswith(name):
            break
    print("validated the path")
    filename,ext = os.path.splitext(name)
    shp = archive.open(filename+'.shp')
    shx = archive.open(filename+'.shx')
    dbf = archive.open(filename+'.dbf')
    reader = shapefile.Reader(shp=shp, shx=shx, dbf=dbf)
    print("read all the shape files")
    try:
        # logger.debug("Entered into try block at shapefile conversion")
        pLogger("INFO", "Entered into try block at shapefile conversion")
        standardized_shapefile = standardize_uploaded_shapefile(reader,
                                                                level=data['level'],
                                                                name_field=data['name_field'],
                                                                iso=data['iso'],
                                                                iso_field=data['iso_field'])
    except Exception as e:
        # logger.debug("Entered into except block at shapefile conversion")
        # logger.exception("An error occurred at standardized_shapefile: %s", str(e))
        print("An error occurred at standardized_shapefile: %s", str(e))
        pLogger("INFO", "Entered into except block at shapefile conversion")
        pLogger("ERROR", "An error occurred at standardized_shapefile:"+ str(e))

    print("out of shapefile method")
    # load the image file
    try:
        screenshot_fileobj = request.FILES['license_screenshot']
    except:
        screenshot_fileobj = None

    # lastly pack these into a zipfile
    zip_path = tempfile.mktemp()
    submit_archive = zipfile.ZipFile(zip_path, mode='w')
    print("created temp zip file")
    
    # add meta file
    meta_path = meta_file.name
    submit_archive.writestr('meta.txt', open(meta_path, mode='rb').read())
    print("added meta file into zip")
    # logger.debug("added meta file into zip")
    pLogger("INFO", "added meta file into zip")

    try:
        # add geojson
        # logger.debug("Entered into try block at geojson generation")
        pLogger("INFO", "Entered into try block at geojson generation")
        shapefile_name = '{}_{}'.format(data['iso'], data['level'])
        geojson_data= standardized_shapefile.to_json()
        print("coverred dataframe into geojson file")
        submit_archive.writestr('{}.geojson'.format(shapefile_name), geojson_data)
        print("added geojson file into zip")
    except Exception as e:
        # logger.debug("Entered into except block at geojson generation")
        # logger.exception("An error occurred at creating jason file: %s", str(e))
        print("An error occurred at creating jason file: %s", str(e))
        pLogger("INFO", "Entered into except block at geojson generation")
        pLogger("ERROR", "An error occurred at creating jason file:"+ str(e))


    # add license screenshot
    if screenshot_fileobj:
        _,ext = os.path.splitext(screenshot_fileobj.name)
        submit_archive.writestr('license{}'.format(ext), screenshot_fileobj.read())

    # Specify the parent directory
    parent_directory = 'geo_{}'.format(get_timehash())
    destination_directory = os.path.join(parent_directory, 'reshaped')

    initial_directory = os.getcwd()
    print("initial directory", initial_directory)

    try:
        # Print the current working directory for debugging
        print(f"Current working directory: {os.getcwd()}")

    except Exception as e:
        print(f"An error occurred printing path: {e}")

    # Check if the parent directory exists
    if not os.path.exists(parent_directory):
        # Create the parent directory if it doesn't exist
        os.makedirs(parent_directory)
        print(f"Parent directory '{parent_directory}' created")

    # Check if the destination directory exists
    if not os.path.exists(destination_directory):
        # Create the destination directory if it doesn't exist
        os.makedirs(destination_directory)
        print(f"Directory '{destination_directory}' created")
    else:
        print(f"Directory '{destination_directory}' already exists.")


    # move the reshaped files from the temporary location to the desired directory
    for name in submit_archive.namelist():
        filename = os.path.basename(name)
        reshaped_file_path = os.path.join(destination_directory, filename)
        with submit_archive.open(name) as source_file, open(reshaped_file_path, 'wb') as destination_file:
            shutil.copyfileobj(source_file, destination_file)

    # close
    submit_archive.close()
    print('zipped files', submit_archive)

    # Create a zip file from the contents of the reshaped files
    zip_filename = os.path.join(parent_directory, '{}_{}.zip'.format(data['iso'], data['level']))
    shutil.make_archive(os.path.splitext(zip_filename)[0], 'zip', destination_directory)

    # submit to github
    release_type = 'gbOpen'
    branch = 'gbContribute-{}-{}_{}-{}'.format(release_type, data['iso'], data['level'], get_timehash())
    submit_title = '{}_{} {}'.format(data['iso'], data['level'], release_type)
    submit_body = '''Boundary data for **{iso}-{level}** submitted through the geoBoundaries contribution form. 
    

**Name**: {name}.
**Affiliation**: {affil}.
**Contact**: {email}.are files validated before they are uploaded to GitHub? Are there protections to ensure that the only expected file types/content can be uploaded?
**Notes about these data**: {notes}
'''.format(iso=data['iso'],
           level=data['level'],
           name=data['contributor_name'],
           affil=data['contributor_affiliation'],
           email=data['contributor_email'],
           notes=data['notes'])
    zip_path_dst = 'sourceData/{}/{}_{}.zip'.format(release_type, data['iso'], data['level'])
    files = {zip_filename:zip_path_dst}
    try:
        # logger.debug("Entered into try block at pull_url")
        pLogger("INFO", "Entered into try block at pull_url")
        pull_url = submit_to_github(branch, submit_title, submit_body, file_size, data, initial_directory, parent_directory, files=files)
    except Exception as e:
        # logger.debug("Entered into except block at pull_url")
        # logger.exception("An error occurred at github fork: %s", str(e))
        print("An error occurred at github fork: %s", str(e))
        pLogger("INFO", "Entered into except block at pull_url")
        pLogger("ERROR", "An error occurred at github fork:"+ str(e))

    return redirect(pull_url)


def create_meta_file(data):
    writer = open(tempfile.mktemp(), mode='w', encoding='utf8')

    lines = []
    line = 'Boundary Representative of Year: {}'.format(data['year'])
    lines.append(line)
    line = 'ISO-3166-1: {}'.format(data['iso'])
    lines.append(line)
    line = 'Boundary Type: {}'.format(data['level'])
    lines.append(line)
    line = 'Canonical Boundary Name: {}'.format(data['canonical'])
    lines.append(line)
    i = 1
    for src in data['source'].split(';'):
        line = 'Source {}: {}'.format(i, src)
        lines.append(line)
        i += 1
    line = 'Release Type: {}'.format(data.get('release_type','gbOpen')) # defaults to gbOpen
    lines.append(line)
    line = 'License: {}'.format(data.get('license',''))
    lines.append(line)
    line = 'License Notes: {}'.format(data.get('license_details',''))
    lines.append(line)
    line = 'License Source: {}'.format(data.get('license_url',''))
    lines.append(line)
    line = 'Link to Source Data: {}'.format(data.get('source_url',''))
    lines.append(line)
    line = 'Other Notes: {}'.format(data.get('notes',''))
    lines.append(line)

    content = '\n'.join(lines)
    writer.write(content)
    
    writer.close()
    return writer

def standardize_uploaded_shapefile(reader, level, name_field, iso=None, iso_field=None):
     
    # Assuming 'reader' is your shapefile reader object
    print("entered into shapefile method")
    records = list(reader.iterShapeRecords())
    # Extract attributes and geometry
    if level != "ADM0" and iso_field != "NONE" and iso_field != " " and iso_field.strip():
        attributes = [{'Name': record.record[name_field], 'Level': level, 'ISO_Code':record.record[iso_field]} for record in records]
        geometries = [shape.shape for shape in records]
    else:
        attributes = [{'Name': record.record[name_field], 'Level': level} for record in records]
        geometries = [shape.shape for shape in records]

    # Create a GeoDataFrameso_field': ['ISO_Code
    gdf = gpd.GeoDataFrame(attributes, geometry=geometries, crs='EPSG:4326')

    print("created the geodataframe")

    if level == 'ADM0':
        gdf['ISO_Code'] = iso
    
    print("returning the frame")

    return gdf

def install_git_lfs():
    try:
        subprocess.run(['git', 'lfs', 'install'], check=True)
        print("Git LFS installed successfully.")
    except subprocess.CalledProcessError as e:
        print(f"Error installing Git LFS: {e}")

def submit_to_github(branchname, title, body, file_size, data, initial_directory, parent_directory, files):
    # init
    g = Github(config('GITHUB_TOKEN'))
    upstream = g.get_repo('wmgeolab/geoBoundaries') # upstream
    upstream_branch = 'main'
    # get or create the fork
    try:
        # get existing fork
        fork = g.get_user().get_repo('geoBoundaries')
    except:
        # fork doesn't already exist, eg if the geoBoundaryBot's fork has been deleted/cleaned up
        fork = g.get_user().create_fork(upstream)
    # create new branch based on upstream
    fork.create_git_ref(ref='refs/heads/' + branchname, 
                        sha=upstream.get_git_ref(ref='heads/' + upstream_branch).object.sha)
    print("created fork")
    # commit files to new branch
    for src,dst in files.items():
        message = 'Add {}'.format(dst)
        src_path = os.path.abspath(src)
        print(src_path)
        content = open(src, mode='rb').read()
        try:
            print("Source", src)
            # logger.debug("Entered into try block in submit method")
            pLogger("INFO", "Entered into try block in submit method")
            # print("Content", content)
            fork.create_file(dst, message, content, branch=branchname)
        except Exception as e:
            # logger.debug("Entered into except block in submit method")
            # logger.exception("An error occurred at github method: %s", str(e))
            print("An error occurred at github method: %s", str(e))
            pLogger("INFO", "Entered into except block in submit method")
            pLogger("ERROR", "An error occurred at github method:"+ str(e))
            # get sha of existing file by inspecting parent folder's git tree.
            # get_contents() is easier but downloads the entire file and fails
            # for larger filesizes.
            dst_folder = os.path.dirname(dst)
            tree_url = 'https://api.github.com/repos/geoBoundaryBot/geoBoundaries/git/trees/{}:{}'.format(branchname, quote_plus(dst_folder))
            print('parent tree url', tree_url)
            # try:
            tree = json.loads(urlopen(tree_url).read())
            # loop files in tree until file is found
            for member in tree['tree']:
                if dst.endswith(member['path']):
                    existing_sha = member['sha']
                    break

            if existing_sha is not None:

                if file_size < 25 * 1024 * 1024:  # Check if content size is less than 30MB
                    fork.update_file(dst, message, content, existing_sha, branch=branchname)

                    # make pull request
                    pull = upstream.create_pull(title, body, base=upstream_branch, head='geoBoundaryBot:'+branchname)
                    print(pull)

                    # return the url
                    pull_url = 'https://github.com/wmgeolab/geoBoundaries/pull/{}'.format(pull.number)

                    directory_to_remove = 'geo'
                    # Get the current directory
                    current_directory = os.getcwd()
                    print(current_directory)

                    # Move back to the original directory (where you started)
                    target_directory = os.path.abspath(os.path.join(current_directory, parent_directory))
                    os.chdir(target_directory)

                    # Print the current directory for debugging
                    print(f"Current directory: {os.getcwd()}")

                    # Check if the directory exists before attempting to remove it
                    if os.path.exists(target_directory):
                        shutil.rmtree(target_directory)
                        print(f"Directory '{directory_to_remove}' removed successfully.")
                    else:
                        print(f"Directory '{directory_to_remove}' does not exist.")

                    # Move back to the original directory (where you started)
                    os.chdir(initial_directory)
                
                elif len(data['iso'])==3 and len(data['level'])==4:
                    print("As the file size is big creating pull reqquest through terminal")
                    # Get the current directory
                    current_directory = os.getcwd()
                    print(current_directory)
                    # #Create a temporary file for reshaped files
                    # temp_zip_path = os.path.abspath("geo/zip/{}_{}.zip".format(data['iso'], data['level']))
                    # source_directory = 'geo/reshaped'
                    # # Create the zip archive
                    # shutil.make_archive(temp_zip_path[:-4], 'zip', source_directory)

                    # Move into the Local Dir
                    # clonepath='geo/gitClone'
                    clonepath = os.path.join(parent_directory, 'gitclone')
                    os.makedirs(clonepath)
                    os.chdir(clonepath)

                    # #Delete the directory SYR2
                    # subdirectory = os.path.abspath("geo/gitClone/geoBoundaries")
                    # if os.path.exists(subdirectory) and os.path.isdir(subdirectory):
                    #     shutil.rmtree(subdirectory)

                    # Set environment variable
                    github_token=(config('GITHUB_TOKEN'))

                    # Call the function to install Git LFS
                    # install_git_lfs()

                    # Enable sparse checkout
                    subprocess.run(['git', 'sparse-checkout', 'init'])

                    # Clone the repository
                    subprocess.run(['git', 'clone', '--filter=blob:none', '--no-checkout', f'https://{github_token}@github.com/wmgeolab/geoBoundaries.git'])

                    # Move into the cloned repository
                    os.chdir('geoBoundaries')

                    # Set up sparse checkout
                    subprocess.run(['git', 'sparse-checkout', 'set', 'sourceData/gbOpen/{}_{}.zip'.format(data['iso'],data['level'])])
                    subprocess.run(['git', 'checkout'])

                    # Create a new branch
                    subprocess.run(['git', 'branch', branchname])

                    # Switch to the new branch
                    subprocess.run(['git', 'checkout', branchname])

                    # Replace the contents of the cloned file with the contents of temp_zip_path
                    shutil.copy(src_path, 'sourceData/gbOpen/{}_{}.zip'.format(data['iso'], data['level']))
                    print("source file", src_path)
                    print("replaced the file")

                    #status
                    subprocess.run(['git', 'status'], check=True)

                    # Commit changes
                    subprocess.run(['git', 'add','sourceData/gbOpen/{}_{}.zip'.format(data['iso'], data['level'])], check=True)

                    try:
                        #commit
                        subprocess.run(['git', 'commit', '-m', message], check=True)

                    except:
                        # Set user details
                        subprocess.run(['git', 'config', 'user.name', 'geoBoundaryBot'], check=True)
                        subprocess.run(['git', 'config', 'user.email', 'geogdan@gmail.com'], check=True)
                        subprocess.run(['git', 'commit', '-m', message], check=True)
                        print("successfully commited in except block")


                    # subprocess.run(['git','push','origin',branchname])
                    subprocess.run(['git', 'push', '--set-upstream', 'origin', branchname], check=True)  

                    pull = upstream.create_pull(title, body, base=upstream_branch, head=branchname)
                    print(pull)

                    # return the url
                    pull_url = 'https://github.com/wmgeolab/geoBoundaries/pull/{}'.format(pull.number)

                    directory_to_remove = 'geo'
                    # Get the current directory
                    current_directory = os.getcwd()
                    print(current_directory)

                    # Move back to the original directory (where you started)
                    target_directory = os.path.abspath(os.path.join(current_directory, '..','..'))
                    os.chdir(target_directory)

                    # Print the current directory for debugging
                    print(f"Current directory: {os.getcwd()}")

                    # Check if the directory exists before attempting to remove it
                    if os.path.exists(target_directory):
                        shutil.rmtree(target_directory)
                        print(f"Directory '{directory_to_remove}' removed successfully.")
                    else:
                        print(f"Directory '{directory_to_remove}' does not exist.")

                    # Move back to the original directory (where you started)
                    os.chdir(initial_directory)

    # return the url
    return pull_url

