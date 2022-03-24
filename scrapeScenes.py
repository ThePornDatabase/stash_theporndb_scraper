#!/usr/bin/python3

import argparse
import base64
import copy
import logging
import re
import sys
import time
import urllib
from datetime import datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

import requests
from PIL import Image

import StashInterface

custom_clean_name = None
if Path(__file__).with_name('custom.py').is_file():
    from custom import clean_name as custom_clean_name

custom_sceneQuery = None
if Path(__file__).with_name('custom_sceneQuery.py').is_file():
    from custom_sceneQuery import sceneQuery as custom_sceneQuery

###########################################################
# CONFIGURATION OPTIONS HAVE BEEN MOVED TO CONFIGURATION.PY#
###########################################################

# MetadataAPI settings
tpdb_sleep = 1  # time to sleep before each API req
tpdb_ua = "stashpy/1.0.0"  # user agent
tpdb_headers = {
    'User-Agent': tpdb_ua,
}

stash_b64_header = "data:image/jpg;base64,"  # actual mime doesn't matter


# Utility Functions
def lreplace(pattern, sub, string):
    """
    Replaces 'pattern' in 'string' with 'sub' if 'pattern' starts 'string'.
    """
    return re.sub('^%s' % pattern, sub, string)


def scrubFileName(file_name):
    scrubbedWords = [
        r'\d{3,4}p', 'MP4-(.+?)$', ' XXX ', 'WMV-(.+?)$', '-UNKNOWN', ' x264-(.+?)$', 'DVDRip',
        'WEBRIP', r'[-._ ]WEB[-._ ]', r'\[PRiVATE\]', 'HEVC', 'x265', 'PRT-xpost', '-xpost',
        r'[-._ ]SD[-._ ]', r'[-._ ]HD[-._ ]', '\'', '&', ' rq', 'SD$', 'HD$'
    ]
    clean_name = re.sub(r'\.', ' ', file_name)  # replace periods
    for word in scrubbedWords:  # delete scrubbedWords
        clean_name = re.sub(word, '', clean_name, 0, re.IGNORECASE)

    # add support for custom name cleaning
    if custom_clean_name is not None:
        clean_name = custom_clean_name(clean_name)

    clean_name = clean_name.strip()  # trim
    return clean_name


def keyIsSet(json_object, fields):  # checks if field exists for json_object.  If "fields" is a list, drills down through a tree defined by the list
    if json_object:
        if isinstance(fields, list):
            for field in fields:
                if field in json_object and json_object[field] is not None:
                    json_object = json_object[field]
                else:
                    return False
            return True
        else:
            if fields in json_object and json_object[fields] is not None:
                return True
    return False


def listToLower(input_list):
    output_list = []
    for item in input_list:
        if isinstance(item, str):
            output_list.append(item.lower())
        else:
            output_list.append(item)
    return output_list


# Script-specific functions
def createStashPerformerData(tpbd_performer):  # Creates stash-compliant data from raw data provided by ThePornDB
    stash_performer = {}
    if keyIsSet(tpbd_performer, ["parent", "name"]):
        stash_performer["name"] = tpbd_performer["parent"]["name"]
    if keyIsSet(tpbd_performer, ["parent", "extras", "birthday"]):
        stash_performer["birthdate"] = tpbd_performer["parent"]["extras"]["birthday"]
    if keyIsSet(tpbd_performer, ["parent", "extras", "measurements"]):
        stash_performer["measurements"] = tpbd_performer["parent"]["extras"]["measurements"]
    if keyIsSet(tpbd_performer, ["parent", "extras", "tattoos"]):
        stash_performer["tattoos"] = tpbd_performer["parent"]["extras"]["tattoos"]
    if keyIsSet(tpbd_performer, ["parent", "extras", "piercings"]):
        stash_performer["piercings"] = tpbd_performer["parent"]["extras"]["piercings"]
    if keyIsSet(tpbd_performer, ["parent", "aliases"]) and len(tpbd_performer["parent"]["aliases"]) > 1:
        stash_performer["aliases"] = tpbd_performer["parent"]["aliases"]
    if keyIsSet(tpbd_performer, ["parent", "extras", "gender"]):
        if tpbd_performer["parent"]["extras"]["gender"] == "Male":
            stash_performer["gender"] = 'MALE'
        if tpbd_performer["parent"]["extras"]["gender"] == "Female":
            stash_performer["gender"] = 'FEMALE'
        if tpbd_performer["parent"]["extras"]["gender"] == "Transgender Male":
            stash_performer["gender"] = 'TRANSGENDER_MALE'
        if tpbd_performer["parent"]["extras"]["gender"] == "Transgender Female":
            stash_performer["gender"] = 'TRANSGENDER_FEMALE'
        if tpbd_performer["parent"]["extras"]["gender"] == "Intersex":
            stash_performer["gender"] = 'INTERSEX'
    return stash_performer


def createStashStudioData(tpbd_studio):  # Creates stash-compliant data from raw data provided by TPBD
    global tpdb_headers
    stash_studio = {}
    if config.compact_studio_names:
        stash_studio["name"] = tpbd_studio["name"].replace(' ', '')
    else:
        stash_studio["name"] = tpbd_studio["name"]
    stash_studio["url"] = tpbd_studio["url"]
    if tpbd_studio["logo"] is not None and "default" not in tpbd_studio["logo"]:
        time.sleep(tpdb_sleep)
        try:
            r = requests.get(tpbd_studio["logo"], proxies=config.proxies, headers=tpdb_headers, timeout=(3, 5))
            if r.status_code >= 400:
                logging.error('ThePornDB HTTP Error: %s' % r.status_code)
                return stash_studio
            image = r.content
            image_b64 = base64.b64encode(image)
            stash_studio["image"] = stash_b64_header + image_b64.decode(ENCODING)
        except requests.exceptions.RequestException as e:
            logging.error('Error Getting Studio Image %s' % e)
            return stash_studio
    return stash_studio


def getJpegImage(image_url):
    try:
        r = requests.get(image_url, stream=True, proxies=config.proxies)
        r.raw.decode_content = True  # handle spurious Content-Encoding
        image = Image.open(r.raw)
        if image.format:
            if image.mode in ('RGBA', 'LA'):
                fill_color = 'black'  # your background
                background = Image.new(image.mode[:-1], image.size, fill_color)
                background.paste(image, image.split()[-1])
                image = background
            buffered = BytesIO()
            image.save(buffered, format="JPEG")
            image = buffered.getvalue()
            return image
    except:
        logging.error("Error Getting Image at URL:" + image_url, exc_info=config.debug_mode)

    return None


def getBabepediaImage(name):
    url = "https://www.babepedia.com/pics/" + urllib.parse.quote(name) + ".jpg"
    if requests.get(url, proxies=config.proxies):
        return getJpegImage(url)
    return None


def getTpbdImage(name):
    url = "https://metadataapi.net/api/performers?q=" + urllib.parse.quote(name)
    time.sleep(tpdb_sleep)
    r = requests.get(url, proxies=config.proxies, timeout=(3, 5), headers=tpdb_headers)
    if r.status_code >= 400:
        logging.error('ThePornDB HTTP Error: %s' % r.status_code)
        return None
    if len(r.json()["data"]) == 1:  # If we only have 1 hit
        raw_data = r.json()["data"][0]
        image_url = raw_data["image"]
        if "default" not in image_url:
            image = getJpegImage(image_url)
            return image
    return None


def getPerformerImageB64(name):  # Searches Babepedia and TPBD for a performer image, returns it as a base64 encoding
    global my_stash, config
    try:
        performer = my_stash.getPerformerByName(name)

        # Try Babepedia if flag is set
        if config.get_images_babepedia:
            # Try Babepedia
            image = getBabepediaImage(name)
            if image:
                image_b64 = base64.b64encode(image)
                return stash_b64_header + image_b64.decode(ENCODING)

            # Try aliases at Babepedia
            if performer and performer.get("aliases", None):
                for alias in performer["aliases"]:
                    image = getBabepediaImage(alias)
                    if image:
                        image_b64 = base64.b64encode(image)
                        return stash_b64_header + image_b64.decode(ENCODING)

        # Try thePornDB
        image = getTpbdImage(name)
        if image:
            image_b64 = base64.b64encode(image)
            return stash_b64_header + image_b64.decode(ENCODING)

        return None
    except:
        logging.error("Error Getting Performer Image", exc_info=config.debug_mode)


def getPerformer(name):
    global tpdb_headers, tpbd_error_count
    search_url = "https://api.metadataapi.net/api/performers?q=" + urllib.parse.quote(name)
    data_url_prefix = "https://api.metadataapi.net/api/performers/"
    try:
        time.sleep(tpdb_sleep)  # sleep before every request to avoid being blocked
        result = requests.get(search_url, proxies=config.proxies, timeout=(3, 5), headers=tpdb_headers)
        tpbd_error_count = 0
        if result.status_code >= 400:
            logging.error('ThePornDB HTTP Error: %s' % result.status_code)
            return None
        if next(iter(result.json().get("data", [{}])), {}).get("id", None):
            performer_id = result.json()["data"][0]["id"]
            return requests.get(data_url_prefix + performer_id, proxies=config.proxies, headers=tpdb_headers).json()["data"]
        else:
            return None
    except ValueError:
        logging.error("Error communicating with ThePornDB")
        tpbd_error_count = tpbd_error_count + 1
        if tpbd_error_count > 3:
            logging.error("ThePornDB seems to be down.  Exiting.")
            sys.exit()


def sceneHashQuery(oshash):  # Scrapes ThePornDB based on oshash.  Returns an array of scenes as results, or None
    global tpdb_headers, tpbd_error_count
    url = "https://api.metadataapi.net/api/scenes?hash=" + urllib.parse.quote(oshash)
    try:
        time.sleep(tpdb_sleep)  # sleep before every request to avoid being blocked
        result = requests.get(url, proxies=config.proxies, timeout=(3, 5), headers=tpdb_headers)
        tpbd_error_count = 0
        if result.status_code >= 400:
            logging.error('ThePornDB HTTP Error: %s' % result.status_code)
            return None
        return result.json()["data"]
    except ValueError:
        logging.error("Error communicating with ThePornDB")
        tpbd_error_count = tpbd_error_count + 1
        if tpbd_error_count > 3:
            logging.error("ThePornDB seems to be down.  Exiting.")
            sys.exit()


def sceneQuery(query, parse_function=True):  # Scrapes ThePornDB based on query.  Returns an array of scenes as results, or None
    global tpdb_headers, tpbd_error_count

    # add support for custom query cleaning
    if custom_sceneQuery is not None:
        query = custom_sceneQuery(query)
    if parse_function:
        url = "https://api.metadataapi.net/api/scenes?parse=" + urllib.parse.quote(query)
    else:
        url = "https://api.metadataapi.net/api/scenes?q=" + urllib.parse.quote(query)
    try:
        # TPDB seems to work better with YYYY-MM-DD instead of YYYYMMDD
        url = url.replace("%20", " ")
        if re.search(r'(\d{8})', url):
            date_string = re.search(r'(\d{8})', url).group(1)
            if date_string:
                if re.search(r'^(20[0-2])', date_string):
                    try:
                        date_pass = datetime.strptime(date_string, '%Y%m%d').strftime('%Y-%m-%d')
                        url = url.replace(date_string, date_pass)
                    except:
                        pass
        url = url.replace(" ", "%20")
        time.sleep(tpdb_sleep)  # sleep before every request to avoid being blocked
        result = requests.get(url, proxies=config.proxies, timeout=(3, 5), headers=tpdb_headers)
        tpbd_error_count = 0
        if result.status_code >= 400:
            logging.error('ThePornDB HTTP Error: %s' % result.status_code)
            return None
        return result.json()["data"]
    except ValueError:
        logging.error("Error communicating with ThePornDB")
        tpbd_error_count = tpbd_error_count + 1
        if tpbd_error_count > 3:
            logging.error("ThePornDB seems to be down.  Exiting.")
            sys.exit()


def manuallyDisambiguateResults(scraped_data):
    print("Found ambiguous result.  Which should we select?:")
    for index, scene in enumerate(scraped_data):
        print(index + 1, end=': ')
        if keyIsSet(scene, ['site', 'name']):
            print(scene['site']['name'] + '\t', end=" ")
        if keyIsSet(scene, ['date']):
            print(scene['date'] + '\t', end=" ")
        if keyIsSet(scene, ['title']):
            print(scene['title'] + '\t', end=" ")
        if keyIsSet(scene, ['last_updated']):
            print("(Updated: " + scene['last_updated'] + ")", end=" ")
        print('')
    print("0: None of the above.  Skip this scene.")

    selection = -1
    while selection < 0 or selection > len(scraped_data):
        try:
            selection = int(input("Selection: "))
            if selection < 0 or selection > len(scraped_data):
                raise ValueError
        except ValueError:
            print("Invalid Selection")

    if selection == 0:
        return scraped_data
    else:
        new_data = [scraped_data[selection - 1]]
        return new_data


def areAliases(first_performer, second_performer, site=None):
    if first_performer.lower() == second_performer.lower():  # No need to conduct checks if they're the same
        return True

    global my_stash, known_aliases, config
    if config.compact_studio_names and site:
        site = site.replace(' ', '')

    first_performer_aliases = [first_performer]
    if known_aliases.get(first_performer, None):
        first_performer_aliases = first_performer_aliases + known_aliases.get(first_performer, None)
    second_performer_aliases = [second_performer]
    if known_aliases.get(second_performer, None):
        second_performer_aliases = second_performer_aliases + known_aliases.get(second_performer, None)
    # build aliases of both first/second performer
    # First performer
    result = my_stash.getPerformerByName(first_performer)
    if result and keyIsSet(result, "aliases"):  # Add Stash Aliases
        first_performer_aliases = list(set(first_performer_aliases + result["aliases"]))
    result = my_stash.scrapePerformerFreeones(first_performer)
    if result and keyIsSet(result, "aliases"):  # Add Freeones Aliases
        first_performer_aliases = list(set(first_performer_aliases + result["aliases"]))
    result = getPerformer(first_performer)
    if result and keyIsSet(result, "aliases"):  # Add TPBD Aliases
        first_performer_aliases = list(set(first_performer_aliases + result["aliases"]))
    # Second Performer
    result = my_stash.getPerformerByName(second_performer)
    if result and keyIsSet(result, "aliases"):  # Add Stash Aliases
        second_performer_aliases = list(set(second_performer_aliases + result["aliases"]))
    result = my_stash.scrapePerformerFreeones(second_performer)
    if result and keyIsSet(result, "aliases"):  # Add Freeones Aliases
        second_performer_aliases = list(set(second_performer_aliases + result["aliases"]))
    result = getPerformer(second_performer)
    if result and keyIsSet(result, "aliases"):  # Add TPBD Aliases
        second_performer_aliases = list(set(second_performer_aliases + result["aliases"]))
    # check if one is an alias of another, but don't compare aliases
    if first_performer in second_performer_aliases or second_performer in first_performer_aliases:
        return True
    first_performer = first_performer + " (" + site + ")"
    second_performer = second_performer + " (" + site + ")"
    if first_performer in second_performer_aliases or second_performer in first_performer_aliases:
        return True
    return False


def getQuery(scene):
    global config
    if config.parse_with_filename:
        try:
            file_path = Path(scene['path'])
            file_name = file_path.stem
            dirs = list(file_path.parts[1:-1])

            if config.clean_filename:
                file_name = scrubFileName(file_name)

            scrape_query = file_name
            for x in range(min(config.dirs_in_query, len(dirs))):
                scrape_query = dirs.pop() + ' ' + scrape_query
        except:
            logging.error(f"Error when parsing scene path: {scene['path']}", exc_info=config.debug_mode)
            return
    else:
        scrape_query = scene['title']
    return '' if scrape_query is None else str(scrape_query)


def scrapeScene(scene):
    global my_stash, config
    scrape_query = ""
    try:
        scene_data = my_stash.createSceneUpdateData(scene)  # Start with our current data as a template
        scraped_data = None
        # if config.use_oshash and scene['oshash']:
        #    scraped_data = sceneHashQuery(scene['oshash'])
        if not scene['date']:
            if my_stash.getFileDate(scene['origpath']):
                scene['date'] = my_stash.getFileDate(scene['origpath'])
        if not scraped_data:
            scrape_query = scrubFileName(getQuery(scene))
            scraped_data = sceneQuery(scrape_query)
        if not scraped_data:
            scraped_data = sceneQuery(scrape_query, False)
        if not scraped_data:
            file_path = Path(scene['path'])
            if file_path.suffix.lower() in ['mp4', 'wmv', 'avi', 'flv', 'mov', 'avchd', 'mpg', 'mpeg']:
                filename = file_path.stem
                scene['path'] = scene['path'].replace(filename, scrubFileName(filename))
            if config.fail_no_date:
                if re.search(r'[-._ ](\d{2}[-._ ]\d{2}[-._ ]\d{2})[-._ ]', scene['path']):
                    scene['path'] = re.sub(r'[-._ ]\d{2}[-._ ]\d{2}[-._ ]\d{2}[-._ ]', r' ', scene['path'])
                    scene['path'] = scene['path'].replace("  ", " ")
                    print("No data found, Retrying without date for: [{}]".format(scrape_query))
                    scrapeScene(scene)
                    return None
                if re.search(r'[-._ ](\d{4}[-._ ]\d{2}[-._ ]\d{2})[-._ ]', scene['path']):
                    scene['path'] = re.sub(r'[-._ ]\d{4}[-._ ]\d{2}[-._ ]\d{2}[-._ ]', r' ', scene['path'])
                    scene['path'] = scene['path'].replace("  ", " ")
                    print("No data found, Retrying without date for: [{}]".format(scrape_query))
                    scrapeScene(scene)
                    return None
                if re.search(r'[- (_.]([012][0-9])|(31)[- (_.]?(0[1-9])|(1[0-2])[- (_.]?((19)|(20))?\d{2}[- (_.]', scene['path']) or re.search(r'[- (_.]((19)|(20))?\d{2}[- (_.]?(0[1-9])|(1[0-2])[- (_.]?([012][0-9])|(31)[- (_.]', scene['path']):
                    scene['path'] = re.sub(r'[- (_.]([012][0-9])|(31)[- (_.]?(0[1-9])|(1[0-2])[- (_.]?((19)|(20))?\d{2}[- (_.]', r' ', scene['path'])
                    scene['path'] = re.sub(r'[- (_.]((19)|(20))?\d{2}[- (_.]?(0[1-9])|(1[0-2])[- (_.]?([012][0-9])|(31)[- (_.]', r' ', scene['path'])
                    scene['path'] = scene['path'].replace("  ", " ")
                    print("No data found, Retrying without date for: [{}]".format(scrape_query))
                    scrapeScene(scene)
                    return None
                if re.search(r'^([012][0-9])|(31)[ -(_.]?(0[1-9])|(1[0-2])[ -(_.]?((19)|(20))?\d{2}[ -(_.]', scene['path']) or re.search(r'^((19)|(20))?\d{2}[ -(_.]?(0[1-9])|(1[0-2])[ -(_.]?([012][0-9])|(31)[ -(_.]', scene['path']):
                    scene['path'] = re.sub(r'[ -(_.]([012][0-9])|(31)[ -(_.]?(0[1-9])|(1[0-2])[ -(_.]?((19)|(20))?\d{2}[ -(_.]', r' ', scene['path'])
                    scene['path'] = re.sub(r'[ -(_.]((19)|(20))?\d{2}[ -(_.]?(0[1-9])|(1[0-2])[ -(_.]?([012][0-9])|(31)[ -(_.]', r' ', scene['path'])
                    scene['path'] = scene['path'].replace("  ", " ")
                    print("No data found, Retrying without date for: [{}]".format(scrape_query))
                    scrapeScene(scene)
                    return None
                if re.search(r'[ -(_.]([012][0-9])|(31)[ -(_.]?(0[1-9])|(1[0-2])[ -(_.]?((19)|(20))?\d{2}$', scene['path']) or re.search(r'[ -(_.]((19)|(20))?\d{2}[ -(_.]?(0[1-9])|(1[0-2])[ -(_.]?([012][0-9])|(31)$', scene['path']):
                    scene['path'] = re.sub(r'[ -(_.]([012][0-9])|(31)[ -(_.]?(0[1-9])|(1[0-2])[ -(_.]?((19)|(20))?\d{2}[ -(_.]', r' ', scene['path'])
                    scene['path'] = re.sub(r'[ -(_.]((19)|(20))?\d{2}[ -(_.]?(0[1-9])|(1[0-2])[ -(_.]?([012][0-9])|(31)[ -(_.]', r' ', scene['path'])
                    scene['path'] = scene['path'].replace("  ", " ")
                    print("No data found, Retrying without date for: [{}]".format(scrape_query))
                    scrapeScene(scene)
                    return None
                else:
                    print("No data found for: [{}]".format(scrape_query))
                    scene_data["tag_ids"].append(my_stash.getTagByName(config.unmatched_tag)['id'])
                    my_stash.updateSceneData(scene_data)
                    return None
            else:
                print("No data found for: [{}]".format(scrape_query))
                scene_data["tag_ids"].append(my_stash.getTagByName(config.unmatched_tag)['id'])
                my_stash.updateSceneData(scene_data)
                return None

        if len(scraped_data) > 1 and not config.parse_with_filename:
            # Try to add studio
            if keyIsSet(scene, "studio"):
                scrape_query = scrape_query + " " + scene['studio']['name']
                new_data = sceneQuery(scrape_query)
                if new_data:
                    scraped_data = new_data

        if len(scraped_data) > 1 and not config.parse_with_filename:
            # Try to and date
            if keyIsSet(scene_data, "date"):
                scrape_query = scrape_query + " " + scene_data['date']
                new_data = sceneQuery(scrape_query)
                if new_data:
                    scraped_data = new_data

        if len(scraped_data) > 1:  # Fix a bug where multiple ThePornDB results are the same scene
            scene_iter = iter(scraped_data)
            next(scene_iter)
            for scraped_scene in scene_iter:
                if scraped_scene['title'] == scraped_data[0]['title']:
                    scraped_data.remove(scraped_scene)

        print("Grabbing Data For: " + scrape_query)

        if len(scraped_data) > 1 and config.manual_disambiguate:  # Manual disambiguate
            scraped_data = manuallyDisambiguateResults(scraped_data)

        if len(scraped_data) > 1 and config.auto_disambiguate:  # Auto disambiguate
            print("Auto disambiguating...")
            print("Matched " + scrape_query + " with " + scraped_data[0]['title'])
            new_data = [scraped_data[0]]
            scraped_data = new_data

        if len(scraped_data) > 1:  # Handling of ambiguous scenes
            print("Ambiguous data found for: [{}], skipping".format(scrape_query))
            if config.ambiguous_tag:
                scene_data["tag_ids"].append(my_stash.getTagByName(config.ambiguous_tag)['id'])
                my_stash.updateSceneData(scene_data)
            return

        if scraped_data:
            scraped_scene = scraped_data[0]
            try:
                scraped_scene = requests.get('https://api.metadataapi.net/api/scenes/' + scraped_scene['id'], proxies=config.proxies, headers=tpdb_headers).json()["data"]
            except:
                logging.error("Exception encountered when getting scene by id '" + scraped_scene['id'], exc_info=config.debug_mode)
                pass
            # If we got new data, update our current data with the new
            updateSceneFromScrape(scene_data, scraped_scene, scene['path'], scene['origpath'])
            print("Success")
        else:
            scene_data["tag_ids"].append(
                my_stash.getTagByName(config.unmatched_tag)['id'])
            my_stash.updateSceneData(scene_data)
            print("No data found for: [{}]".format(scrape_query))
    except:
        logging.error("Exception encountered when scraping '" + scrape_query, exc_info=config.debug_mode)


def manConfirmAlias(scraped_performer, site):  # Returns scraped_performer if response is positive, None otherwise.  If Always or Site are selected, scraped_performer is updated to include a new alias
    global known_aliases, config
    if config.compact_studio_names:
        site = site.replace(' ', '')
    response = input("Found " + scraped_performer['name'] + " as a performer in scene, which TPBD indicates is an alias of " + scraped_performer['parent']['name'] + ".  Should we trust that? (Y)es / (N)o / (A)lways / Always for this (S)ite:")
    if response == 'y' or response == 'Y' or response == 'Yes' or response == 'yes':
        return scraped_performer
    elif response == 'a' or response == 'A' or response == 'always' or response == 'Always':
        # Update our global var
        known_alias_entry = known_aliases.get(scraped_performer['parent']['name'], None)
        if known_alias_entry:
            known_aliases[scraped_performer['parent']['name']].append(scraped_performer['name'])
        else:
            known_aliases[scraped_performer['parent']['name']] = [scraped_performer['name']]
        if keyIsSet(scraped_performer, ["parent", "aliases"]):
            scraped_performer["parent"]['aliases'].append(scraped_performer['name'])
        else:
            scraped_performer["parent"]['aliases'] = [scraped_performer['name']]
        return scraped_performer
    elif response == 's' or response == 'S' or response == 'Site' or response == 'site':
        # Update our global var
        known_alias_entry = known_aliases.get(scraped_performer['parent']['name'], None)
        if known_alias_entry:
            known_aliases[scraped_performer['parent']['name']].append(scraped_performer['name'] + " (" + site + ")")
        else:
            known_aliases[scraped_performer['parent']['name']] = [scraped_performer['name'] + " (" + site + ")"]
        if keyIsSet(scraped_performer, ["parent", "aliases"]):
            scraped_performer["parent"]['aliases'].append(scraped_performer['name'] + " (" + site + ")")
        else:
            scraped_performer["parent"]['aliases'] = [scraped_performer['name'] + " (" + site + ")"]
        return scraped_performer
    return None


def addPerformer(scraped_performer):  # Adds performer using TPDB data, returns ID of performer
    global config
    stash_performer_data = createStashPerformerData(scraped_performer)
    if config.scrape_performers_freeones:
        freeones_data = my_stash.scrapePerformerFreeones(scraped_performer['parent']['name'])
        if freeones_data:
            if keyIsSet(freeones_data, "aliases") and keyIsSet(scraped_performer, ["parent", "aliases"]):
                freeones_data['aliases'] = list(set(freeones_data['aliases'] + scraped_performer["parent"]['aliases']))
            stash_performer_data.update(freeones_data)

    stash_performer_data["image"] = getPerformerImageB64(scraped_performer['parent']['name'])
    return my_stash.addPerformer(stash_performer_data)


def updateSceneFromScrape(scene_data, scraped_scene, path="", origpath=""):
    global config
    tag_ids_to_add = []
    tags_to_add = []
    performer_names = []
    try:
        if config.ambiguous_tag:
            ambiguous_tag_id = my_stash.getTagByName(config.ambiguous_tag)['id']
            if ambiguous_tag_id in scene_data["tag_ids"]:
                scene_data["tag_ids"].remove(ambiguous_tag_id)  # Remove ambiguous tag; it will be readded later if the scene is still ambiguous
        if config.unmatched_tag:
            unmatched_tag_id = my_stash.getTagByName(config.unmatched_tag)['id']
            if unmatched_tag_id in scene_data["tag_ids"]:
                scene_data["tag_ids"].remove(unmatched_tag_id)  # Remove unmatched tag
        if my_stash.getTagByName(config.unconfirmed_alias)["id"] in scene_data["tag_ids"]:
            scene_data["tag_ids"].remove(my_stash.getTagByName(config.unconfirmed_alias)["id"])  # Remove unconfirmed alias tag; it will be readded later if needed

        if config.set_details:
            scene_data["details"] = scraped_scene["description"]  # Add details
        if config.set_date:
            #  Check for file date in case of --prefer_file_date
            scene_data["date"] = ''
            if config.prefer_file_date and origpath:
                filedate = my_stash.getFileDate(origpath)
                if filedate:
                    if filedate < scraped_scene["date"]:
                        print(f"Using filename date of {filedate} instead of scraped result of {scraped_scene['date']} due to preferences")
                        scene_data["date"] = filedate
            if not scene_data["date"]:
                scene_data["date"] = scraped_scene["date"]  # Add date

        if config.set_url:
            scene_data["url"] = scraped_scene["url"]  # Add URL
        if config.set_cover_image and keyIsSet(scraped_scene, ["background", config.background_size]) and not re.search(r'default\d\.png|default\.png', scraped_scene["background"][config.background_size]):  # Add cover_image
            cover_image = getJpegImage(scraped_scene["background"][config.background_size])
            if cover_image:
                image_b64 = base64.b64encode(cover_image)
                scene_data["cover_image"] = stash_b64_header + image_b64.decode(ENCODING)

        # Add Studio to the scene
        if config.set_studio and keyIsSet(scraped_scene, "site"):
            studio_id = None
            scraped_studio = scraped_scene['site']
            if config.compact_studio_names:
                scraped_studio['name'] = scraped_studio['name'].replace(' ', '')
            stash_studio = my_stash.getStudioByName(scraped_studio['name'])
            if stash_studio:
                studio_id = stash_studio["id"]
            elif config.add_studio:
                # Add the Studio to Stash
                print("Did not find " + scraped_studio['name'] + " in Stash.  Adding Studio.")
                studio_id = my_stash.addStudio((createStashStudioData(scraped_studio)))
            if studio_id is not None:  # If we have a valid ID, add studio to Scene
                scene_data["studio_id"] = studio_id

        # Add Tags to the scene
        if config.scrape_tag:
            tags_to_add.append({'tag': config.scrape_tag})
        if config.set_tags and keyIsSet(scraped_scene, "tags"):
            tags_to_add = tags_to_add + scraped_scene["tags"]

        # Add performers to scene
        if config.set_performers and keyIsSet(scraped_scene, "performers"):
            scraped_performer_ids = []
            for scraped_performer in scraped_scene["performers"]:
                not_female = False

                if keyIsSet(scraped_performer, ["parent", "extras"]) and (not keyIsSet(scraped_performer, ["parent", "extras", "gender"]) or scraped_performer["parent"]["extras"]["gender"] != 'Female'):
                    not_female = True

                if (not keyIsSet(scraped_performer, ["parent", "extras", "gender"]) and
                        keyIsSet(scraped_performer, ["extra", "gender"]) and
                        scraped_performer["extra"]["gender"] == 'Male'):
                    not_female = True

                if (config.only_add_female_performers and not scraped_performer['name'].lower() in path.lower() and not_female):
                    continue  # End current loop on male performers not in path

                performer_id = None
                performer_name = scraped_performer['name']
                stash_performer = my_stash.getPerformerByName(performer_name)
                add_this_performer = False
                if stash_performer:
                    performer_id = stash_performer["id"]  # If performer already exists, use that
                    if config.male_performers_in_title or not not_female:
                        performer_names.append(performer_name)  # Add to list of performers in scene
                elif keyIsSet(
                        scraped_performer, ['parent', 'name']):  # If site name does not match someone in Stash and TPBD has a linked parent
                    if (  # Test for when we should automatically accept the parent name
                            areAliases(scraped_performer['name'], scraped_performer['parent']['name'], scraped_scene['site']['name'].replace(' ', '') if config.compact_studio_names else scraped_scene['site']['name']) or  # Parent performer seems to be a valid alias to site performer
                            " " not in scraped_performer['name'] or  # Single name, so we just trust TPBD
                            config.trust_tpbd_aliases  # Flag says to just trust TPBD
                    ):
                        performer_name = scraped_performer['parent'][
                            'name']  # Adopt the parent name
                        stash_performer = my_stash.getPerformerByName(
                            performer_name)
                        if stash_performer:
                            performer_id = stash_performer["id"]  # If performer already exists, use that
                            if config.male_performers_in_title or not not_female:
                                performer_names.append(performer_name)  # Add to list of performers in scene
                        else:
                            add_this_performer = True
                    else:  # We can't automatically trust the parent name.  Ask for manual confirmation if flag is set.
                        if config.confirm_questionable_aliases:
                            confirmed_performer = manConfirmAlias(
                                scraped_performer,
                                scraped_scene['site']["name"])
                            if confirmed_performer:
                                performer_name = scraped_performer['parent']['name']  # Adopt the parent name
                                stash_performer = my_stash.getPerformerByName(performer_name)
                                if stash_performer:
                                    performer_id = stash_performer["id"]  # If performer already exists, use that
                                    if config.male_performers_in_title or not not_female:
                                        performer_names.append(performer_name)  # Add to list of performers in scene
                                    stash_performer.update(createStashPerformerData(confirmed_performer))
                                    my_stash.updatePerformer(stash_performer)  # Update the performer to capture new aliases if needed
                                else:
                                    add_this_performer = True
                        else:
                            print(
                                "Found " + scraped_performer['name'] +
                                " in scene, which TPBD says is an alias of " +
                                scraped_performer['parent']['name'] +
                                ".  However, that couldn't be verified, so skipping addition and tagging scene.  To overwrite, manually add the performer and alias in stash, or set trust_tpbd_aliases or confirm_questionable_aliases to True in your configuration.py"
                            )
                            tag_id = my_stash.getTagByName(
                                "ThePornDB Unconfirmed Alias", True)["id"]
                            scene_data["tag_ids"].append(tag_id)
                            if performer_name.lower() in path.lower():  # If the ambiguous performer is in the file name, put them in the title too.
                                performer_names.append(performer_name)

                # Add ambigous performer tag if we meet relevant requirements
                if (not stash_performer and  # We don't have a match so far
                        not keyIsSet(scraped_performer, ['parent', 'name'])
                        and  # No TPBD parent
                        config.
                                tag_ambiguous_performers  # Config says tag no parent
                ):
                    print(performer_name + " was not found in Stash. However, " + performer_name + " is not linked to a known (multi-site) performer at ThePornDB.  Skipping addition and tagging scene.")
                    tag_id = my_stash.getTagByName("ThePornDB Ambiguous Performer: " + performer_name, True)["id"]
                    scene_data["tag_ids"].append(tag_id)
                    if performer_name.lower() in path.lower():  # If the ambiguous performer is in the file name, put them in the title too.
                        performer_names.append(performer_name)

                # Add performer if we meet relevant requirements
                if add_this_performer and config.add_performers:
                    print("Did not find " + performer_name +
                          " in Stash.  Adding performer.")
                    performer_id = addPerformer(scraped_performer)
                    if config.male_performers_in_title or not not_female:
                        performer_names.append(performer_name)

                if performer_id:  # If we have a valid ID, add performer to Scene
                    scraped_performer_ids.append(performer_id)
            scene_data["performer_ids"] = list(
                set(scene_data["performer_ids"] + scraped_performer_ids))

        # Set Title
        if config.set_title:
            title_prefix = ""
            if config.include_performers_in_title:
                if len(performer_names) > 2:
                    title_prefix = "{}, and {} ".format(
                        ", ".join(performer_names[:-1]), performer_names[-1])
                elif len(performer_names) == 2:
                    title_prefix = performer_names[
                                       0] + " and " + performer_names[1] + " "
                elif len(performer_names) == 1:
                    title_prefix = performer_names[0] + " "
                for name in performer_names:
                    scraped_scene["title"] = lreplace(name, '', scraped_scene["title"]).strip()
            scene_data["title"] = str(title_prefix + scraped_scene["title"]).strip()

        # Set tag_ids for tags_to_add
        for tag_dict in tags_to_add:
            tag_name = tag_dict['tag'].replace('-', ' ').replace('(', '').replace(')', '').strip().title()
            if config.add_tags:
                tag_id = my_stash.getTagByName(tag_name, add_tag_if_missing=True)["id"]
            else:
                stash_tag = my_stash.getTagByName(tag_name, add_tag_if_missing=False)
                if stash_tag:
                    tag_id = stash_tag["id"]
                else:
                    tag_id = None
            if tag_id:  # If we have a valid ID, add tag to Scene
                tag_ids_to_add.append(tag_id)
            else:
                logging.debug("Tried to add tag \'" + tag_dict['tag'] + "\' but failed to find ID in Stash.")
        scene_data["tag_ids"] = list(set(scene_data["tag_ids"] + tag_ids_to_add))
        if config.remove_search_tag and len(required_tags) > 0:
            for remove_tag in required_tags:
                remove_tag_stash = my_stash.getTagByName(remove_tag, add_tag_if_missing=False)
                scene_data["tag_ids"].remove(remove_tag_stash["id"])

        logging.debug("Now updating scene with the following data:")
        logging.debug(scene_data)
        my_stash.updateSceneData(scene_data)
    except:
        logging.error("Scrape succeeded, but update failed:", exc_info=config.debug_mode)


class config_class:
    ###############################################
    # DEFAULT CONFIGURATION OPTIONS.  DO NOT EDIT #
    ###############################################
    use_https = False  # Set to false for HTTP
    server_ip = "<IP ADDRESS>"
    server_port = "<PORT>"
    username = ""
    password = ""
    ignore_ssl_warnings = True  # Set to True if your Stash uses SSL w/ a self-signed cert

    scrape_tag = "Scraped From ThePornDB"  # Tag to be added to scraped scenes.  Set to None to disable
    unmatched_tag = "Missing From ThePornDB"  # Tag to be added to scenes that aren't matched at TPDB.  Set to None to disable.
    disambiguate_only = False  # Set to True to run script only on scenes tagged due to ambiguous scraping. Useful for doing manual disambgiuation.  Must set ambiguous_tag for this to work
    verify_aliases_only = False  # Set to True to scrape only scenes that were skipped due to unconfirmed aliases - set confirm_questionable_aliases to True before using
    rescrape_scenes = False  # If False, script will not rescrape scenes previously scraped successfully.  Must set scrape_tag for this to work
    retry_unmatched = False  # If False, script will not rescrape scenes previously unmatched.  Must set unmatched_tag for this to work
    background_size = 'full'  # Which size get from API, available options: full, large, medium, small
    debug_mode = False
    scrape_organized = False  # If False, script will not scrape scenes set as Organized
    scrape_stash_id = False  # If False, script will not scrape scenes that have a stash_id

    # Set what fields we scrape
    set_details = True
    set_date = True
    set_cover_image = True
    set_performers = True
    set_studio = True
    set_tags = True
    set_title = True
    set_url = True

    # ThePornDB API Key
    tpdb_api_key = ""  # Add your API Key here eg tbdb_api_key = "myactualapikey"

    # Set what content we add to Stash, if found in ThePornDB but not in Stash
    add_studio = True
    add_tags = False  # Script will still add scrape_tag and ambiguous_tag, if set.  Will also tag ambiguous performers if set to True.
    add_performers = True

    # Disambiguation options
    # The script tries to disambiguate using title, studio, and date (or just filename if parse_with_filename is True).  If this combo still returns more than one result, these options are used.  Set both to False to skip scenes with ambiguous results
    auto_disambiguate = False  # Set to True to try to pick the top result from ThePornDB automatically.  Will not set ambiguous_tag
    manual_disambiguate = False  # Set to True to prompt for a selection.  (Overwritten by auto_disambiguate)
    ambiguous_tag = "ThePornDB Ambiguous"  # Tag to be added to scenes we skip due to ambiguous scraping.  Set to None to disable
    # Disambiguation options for when a specific performer can't be verified
    tag_ambiguous_performers = True  # If True, will tag ambiguous performers (performers listed on ThePornDB only for a single site, not across sites)
    confirm_questionable_aliases = True  # If True, when TPBD lists an alias that we can't verify, manually prompt for config.  Otherwise they are tagged for later reprocessing
    trust_tpbd_aliases = True  # If True, when TPBD lists an alias that we can't verify, just trust TBPD to be correct.  May lead to incorrect tagging

    # Other config options
    parse_with_filename = True  # If True, will query ThePornDB based on file name, rather than title, studio, and date
    dirs_in_query = 0  # The number of directories up the path to be included in the query for a filename parse query.  For example, if the file  is at \performer\mysite\video.mp4 and dirs_in_query is 1, query would be "mysite video."  If set to two, query would be "performer mysite video", etc.
    only_add_female_performers = True  # If True, only female performers are added (note, exception is made if performer name is already in title and name is found on ThePornDB)
    scrape_performers_freeones = True  # If True, will try to scrape newly added performers with the freeones scraper
    get_images_babepedia = True  # If True, will try to grab an image from babepedia before the one from ThePornDB
    include_performers_in_title = True  # If True, performers will be added at the beggining of the title
    male_performers_in_title = False  # If True, male performers and included in the title
    clean_filename = True  # If True, will try to clean up filenames before attempting scrape. Often unnecessary, as ThePornDB already does this
    compact_studio_names = True  # If True, this will remove spaces from studio names added from ThePornDB
    fail_no_date = False  # If True, on a failed scrape the system will attempt to remove the date from the query and try a re-scrape
    prefer_file_date = False  # If True, will use the regex date from filename if less than the date on TPDB.  (Many TPDB dates are date of import rather than release date since the website may not list the release date)   ** VERIFY DATE REGEXES ARE GOOD FOR YOUR IMPORT FILES BEFORE ENABLING! **
    remove_search_tag = False  # If True, this will remove tags that are used for manual scraping on a successful scrape.  BE VERY CAREFUL WITH THIS FLAG!
    proxies = {}  # Leave empty or specify proxy like this: {'http':'http://user:pass@10.10.10.10:8000','https':'https://user:pass@10.10.10.10:8000'}
    path_include = False  # filepath to scrape.  This is pointing to path in the already existing Stash database entry, and isn't an import process

    # use_oshash = False # Set to True to use oshash values to query NOT YET SUPPORTED

    def loadConfig(self):
        try:  # Try to load configuration.py values
            import configuration
            for key, value in vars(configuration).items():
                if key[0:2] == "__":
                    continue
                if (key == "server_ip" or key == "server_port") and ("<" in value or ">" in value):
                    logging.warning("Please remove '<' and '>' from your server_ip and server_port lines in configuration.py")
                    sys.exit()
                if value is None or isinstance(
                        value, type(vars(config_class).get(key, None))):
                    vars(self)[key] = value
                else:
                    logging.warning("Invalid configuration parameter: " + key, exc_info=config_class.debug_mode)
            return True
        except ImportError:
            logging.error("No configuration found.  Double check your configuration.py file exists.")
            create_config = input("Create configuruation.py? (yes/no):")
            if create_config == 'y' or create_config == 'Y' or create_config == 'Yes' or create_config == 'yes':
                self.createConfig()
            else:
                logging.error("No configuration found.  Exiting.")
                sys.exit()
        except NameError:
            logging.error("Invalid configuration.py.  Make sure you use 'True' and 'False' (capitalized)", exc_info=config_class.debug_mode)
            sys.exit()

    def createConfig(self):
        self.server_ip = input("What's your Stash server's IP address? (no port please):")
        self.server_port = input("What's your Stash server's port?:")
        https_input = input("Does your Stash server use HTTPS? (yes/no):")
        self.use_https = False
        if https_input == 'y' or https_input == 'Y' or https_input == 'Yes' or https_input == 'yes':
            self.use_https = True
        self.username = input("What's your Stash server's username? (Just press enter if you don't use one):")
        self.password = input("What's your Stash server's password? (Just press enter if you don't use one):")

        server_configuration = r"""
#Server configuration
use_https = {4} # Set to False for HTTP
server_ip= "{0}"
server_port = "{1}"
username="{2}"
password="{3}"
ignore_ssl_warnings= True # Set to True if your Stash uses SSL w/ a self-signed cert
""".lstrip().format(self.server_ip, self.server_port, self.username, self.password, self.use_https)

        configuration = r"""
# Configuration options
scrape_tag= "Scraped From ThePornDB"  #Tag to be added to scraped scenes.  Set to None to disable
unmatched_tag = "Missing From ThePornDB" #Tag to be added to scenes that aren't matched at TPDB.  Set to None to disable.
disambiguate_only = False # Set to True to run script only on scenes tagged due to ambiguous scraping. Useful for doing manual disambgiuation.  Must set ambiguous_tag for this to work
verify_aliases_only = False # Set to True to scrape only scenes that were skipped due to unconfirmed aliases - set confirm_questionable_aliases to True before using
rescrape_scenes= False # If False, script will not rescrape scenes previously scraped successfully.  Must set scrape_tag for this to work
retry_unmatched = False # If False, script will not rescrape scenes previously unmatched.  Must set unmatched_tag for this to work
background_size = 'full' # Which size get from API, available options: full, large, medium, small
debug_mode = False
scrape_organized = False # If False, script will not scrape scenes set as Organized
scrape_stash_id = False # If False, script will not scrape scenes that have a stash_id

#Set what fields we scrape
set_details = True
set_date = True
set_cover_image = True
set_performers = True
set_studio = True
set_tags = True
set_title = True
set_url = True

#ThePornDB API Key
tpdb_api_key = ""

#Set what content we add to Stash, if found in ThePornDB but not in Stash
add_studio = True
add_tags = False  # Script will still add scrape_tag and ambiguous_tag, if set.  Will also tag ambiguous performers if set to True.
add_performers = True

#Disambiguation options
#The script tries to disambiguate using title, studio, and date (or just filename if parse_with_filename is True).  If this combo still returns more than one result, these options are used.  Set both to False to skip scenes with ambiguous results
auto_disambiguate = False  #Set to True to try to pick the top result from ThePornDB automatically.  Will not set ambiguous_tag
manual_disambiguate = False #Set to True to prompt for a selection.  (Overwritten by auto_disambiguate)
ambiguous_tag = "ThePornDB Ambiguous" #Tag to be added to scenes we skip due to ambiguous scraping.  Set to None to disable
#Disambiguation options for when a specific performer can't be verified
tag_ambiguous_performers = True  # If True, will tag ambiguous performers (performers listed on ThePornDB only for a single site, not across sites)
confirm_questionable_aliases = True #If True, when TPBD lists an alias that we can't verify, manually prompt for config.  Otherwise they are tagged for later reprocessing
trust_tpbd_aliases = True #If True, when TPBD lists an alias that we can't verify, just trust TBPD to be correct.  May lead to incorrect tagging

#Other config options
parse_with_filename = True # If True, will query ThePornDB based on file name, rather than title, studio, and date
dirs_in_query = 0 # The number of directories up the path to be included in the query for a filename parse query.  For example, if the file  is at \performer\mysite\video.mp4 and dirs_in_query is 1, query would be "mysite video."  If set to two, query would be "performer mysite video", etc.
only_add_female_performers = True  #If True, only female performers are added (note, exception is made if performer name is already in title and name is found on ThePornDB)
scrape_performers_freeones = True #If True, will try to scrape newly added performers with the freeones scraper
get_images_babepedia = True #If True, will try to grab an image from babepedia before the one from ThePornDB
include_performers_in_title = True #If True, performers will be added at the beggining of the title
male_performers_in_title = False # If True, male performers and included in the title
clean_filename = True #If True, will try to clean up filenames before attempting scrape. Often unnecessary, as ThePornDB already does this
compact_studio_names = True # If True, this will remove spaces from studio names added from ThePornDB
fail_no_date = False #If True, on a failed scrape the system will attempt to remove the date from the query and try a re-scrape
prefer_file_date = False  # If True, will use the regex date from filename if less than the date on TPDB.  (Many TPDB dates are date of import rather than release date since the website may not list the release date)  ** VERIFY DATE REGEXES ARE GOOD FOR YOUR IMPORT FILES BEFORE ENABLING! **
remove_search_tag = False # If True, this will remove tags that are used for manual scraping on a successful scrape.  BE VERY CAREFUL WITH THIS FLAG!
proxies={} # Leave empty or specify proxy like this: {'http':'http://user:pass@10.10.10.10:8000','https':'https://user:pass@10.10.10.10:8000'}
# use_oshash = False # Set to True to use oshash values to query NOT YET SUPPORTED
"""
        with open("configuration.py", "w") as f:
            f.write(server_configuration + configuration)
        print("Configuration file created.  All values are currently at defaults.  It is highly recommended that you edit the configuration.py to your liking.  Otherwise, just re-run the script to use the defaults.")
        sys.exit()


def parseArgs(args):
    my_parser = argparse.ArgumentParser(description='Scrape Stash Scenes from ThePornDB')

    # Add the arguments
    my_parser.add_argument(
        'query',
        nargs='*',
        default="",
        metavar='query',
        type=str,
        help='Query string to pass to the Stash Scene "Find" box')
    my_parser.add_argument('-d',
                           '--debug',
                           action='store_true',
                           help='enable debugging')
    my_parser.add_argument('-r',
                           '--rescrape',
                           action='store_true',
                           help='rescrape already scraped scenes')
    my_parser.add_argument('-nr',
                           '--no_rescrape',
                           action='store_true',
                           help='do not rescrape already scraped scenes')
    my_parser.add_argument('-ru',
                           '--retry_unmatched',
                           action='store_true',
                           help='retry previously unmatched scenes')
    my_parser.add_argument('-ruo',
                           '--retry_unmatched_only',
                           action='store_true',
                           help='only retry previously unmatched scenes')
    my_parser.add_argument('-no',
                           '--new_only',
                           action='store_true',
                           help='only scan previously unscanned scenes')
    my_parser.add_argument('-ao',
                           '--verify_aliases_only',
                           action='store_true',
                           help='scrape only scenes with performers that need to be verified')
    my_parser.add_argument('-do',
                           '--disambiguate_only',
                           action='store_true',
                           help='scrape only scenes tagged as ambiguous')
    my_parser.add_argument('-max',
                           '--max_scenes',
                           metavar='max_scenes',
                           default=0,
                           type=int,
                           help='maximum number of scenes to scrape')
    my_parser.add_argument('-p',
                           '--path_include',
                           metavar='path_include',
                           type=str,
                           help='only search files with this string in the filepath (useful for an import directory)')
    my_parser.add_argument('-fnd',
                           '--fail_no_date',
                           action='store_true',
                           help='retry failed match without date in query')
    my_parser.add_argument('-pfd',
                           '--prefer_file_date',
                           action='store_true',
                           help='prefer date from file if lower than TPDB returned date')
    my_parser.add_argument(
        '-t',
        '--tags',
        metavar='search_tags',
        type=str,
        default=[],
        action='append',
        help='only match scenes with these tags; repeat once for each required tag')
    my_parser.add_argument('-rst',
                           '--remove_search_tag',
                           action='store_true',
                           help='remove search tags on successful scrape (*CAREFUL WITH THIS FLAG*)')
    my_parser.add_argument(
        '-nt',
        '--not_tags',
        metavar='not_tags',
        type=str,
        default=[],
        action='append',
        help='do not match scenes with these tags; repeat once for each excluded tag'
    )
    my_parser.add_argument(
        '-md',
        '--man_disambiguate',
        action='store_true',
        help='prompt to manually select a scene when a single result isn\'t found')
    my_parser.add_argument(
        '-ad',
        '--auto_disambiguate',
        action='store_true',
        help='automatically  select the top scene when a single result isn\'t found'
    )
    my_parser.add_argument(
        '-mv',
        '--man_verify_aliases',
        action='store_true',
        help='prompt to manually confirm an alias when automatic verification fails'
    )

    # Execute the parse_args() method to collect our args
    parsed_args = my_parser.parse_args(args)
    # Set variables accordingly
    global config, max_scenes, required_tags, excluded_tags
    if parsed_args.debug:
        config.debug_mode = True
    if parsed_args.rescrape:
        config.rescrape_scenes = True
    if parsed_args.retry_unmatched:
        config.retry_unmatched = True
    if parsed_args.retry_unmatched_only:
        config.retry_unmatched = True
        required_tags.append(config.unmatched_tag)
    if parsed_args.new_only:
        config.retry_unmatched = False
        excluded_tags.append(config.ambiguous_tag)
    if parsed_args.no_rescrape:
        config.rescrape_scenes = False
    if parsed_args.disambiguate_only:
        config.disambiguate_only = True
        config.manual_disambiguate = True
    if parsed_args.man_disambiguate:
        config.manual_disambiguate = True
    if parsed_args.auto_disambiguate:
        config.auto_disambiguate = True
    if parsed_args.man_verify_aliases:
        config.manConfirmAlias = True
    if parsed_args.verify_aliases_only:
        config.verify_aliases_only = True
        config.manConfirmAlias = True
    if parsed_args.max_scenes:
        max_scenes = parsed_args.max_scenes
    for tag in parsed_args.tags:
        required_tags.append(tag)
    for tag in parsed_args.not_tags:
        excluded_tags.append(tag)
    if parsed_args.fail_no_date:
        config.fail_no_date = True
    if parsed_args.prefer_file_date:
        config.prefer_file_date = True
    if parsed_args.path_include:
        config.path_include = parsed_args.path_include
    if parsed_args.remove_search_tag:
        config.remove_search_tag = True
    return parsed_args.query


# Globals
tpbd_error_count = 0
my_stash = None
ENCODING = 'utf-8'
known_aliases = {}
required_tags = []
excluded_tags = []
max_scenes = 0
config = config_class()


def main(args):
    logging.basicConfig(level=logging.DEBUG)
    global my_stash, max_scenes, required_tags, excluded_tags, config, tpbd_error_count, tpdb_headers
    scenes_with_tags = []
    scenes_without_tags = []
    try:
        tpbd_error_count = 0
        config.loadConfig()
        scenes = None
        if config.tpdb_api_key != "":
            tpdb_headers['Authorization'] = 'Bearer ' + config.tpdb_api_key
            logging.info('API Key found for TPDB')
        else:
            print("TPDB API Key not set. Exiting.")
            return

        query_args = parseArgs(args)
        if len(query_args) == 1:
            query = "\"" + query_args[0] + "\""
        else:
            query = ' '.join(query_args)

        if not config.debug_mode:
            logging.getLogger().setLevel("WARNING")

        if config.use_https:
            server = 'https://' + str(config.server_ip) + ':' + str(config.server_port)
        else:
            server = 'http://' + str(config.server_ip) + ':' + str(config.server_port)

        my_stash = StashInterface.stash_interface(server, config.username, config.password, config.ignore_ssl_warnings)

        if len(config.proxies) > 0:
            my_stash.setProxies(config.proxies)

        if config.ambiguous_tag:
            my_stash.getTagByName(config.ambiguous_tag, True)
        if config.scrape_tag:
            scrape_tag_id = my_stash.getTagByName(config.scrape_tag, True)["id"]
        if config.unmatched_tag:
            unmatched_tag_id = my_stash.getTagByName(config.unmatched_tag, True)["id"]
        config.unconfirmed_alias = my_stash.getTagByName("ThePornDB Unconfirmed Alias", True)["name"]

        findScenes_params = {'filter': {'q': query, 'sort': "created_at", 'direction': 'DESC'}, 'scene_filter': {}}
        if max_scenes != 0:
            findScenes_params['max_scenes'] = max_scenes
        if config.path_include:
            findScenes_params['scene_filter']['path'] = {'modifier': 'INCLUDES', 'value': config.path_include}

        if config.disambiguate_only:  # If only disambiguating scenes
            required_tags.append(config.ambiguous_tag)
        if config.verify_aliases_only:  # If only disambiguating aliases
            required_tags.append(config.unconfirmed_alias)
        if not config.retry_unmatched:  # If not retrying unmatched scenes
            excluded_tags.append(config.unmatched_tag)
        if not config.rescrape_scenes:  # If only scraping unscraped scenes
            excluded_tags.append(config.scrape_tag)

        my_stash.waitForIdle()  # Wait for Stash to idle before scraping

        # Set our filter to require any required_tags
        if len(required_tags) > 0:
            findScenes_params_incl = copy.deepcopy(findScenes_params)
            required_tag_ids = []
            for tag_name in required_tags:
                tag = my_stash.getTagByName(tag_name, False)
                if tag:
                    required_tag_ids.append(tag["id"])
                else:
                    logging.error("Did not find tag in Stash: " + tag_name, exc_info=config.debug_mode)
            findScenes_params_incl['scene_filter']['tags'] = {'modifier': 'INCLUDES', 'value': [*required_tag_ids]}
            if not config.scrape_stash_id:  # include only scenes without stash_id
                findScenes_params_incl['scene_filter']['stash_id'] = {'modifier': 'IS_NULL', 'value': 'none'}
            if not config.scrape_organized:  # include only scenes that are not organized
                findScenes_params_incl['scene_filter']['organized'] = False
            if len(excluded_tags) > 0:
                print("Getting Scenes With Required Tags")
            scenes_with_tags = my_stash.findScenes(**findScenes_params_incl)
            scenes = scenes_with_tags
        # Set our filter to exclude any excluded_tags
        if len(excluded_tags) > 0:
            findScenes_params_excl = copy.deepcopy(findScenes_params)
            excluded_tag_ids = []
            for tag_name in excluded_tags:
                tag = my_stash.getTagByName(tag_name, False)
                if tag:
                    excluded_tag_ids.append(tag["id"])
                else:
                    logging.error("Did not find tag in Stash: " + tag_name, exc_info=config.debug_mode)
            findScenes_params_excl['scene_filter']['tags'] = {'modifier': 'EXCLUDES', 'value': [*excluded_tag_ids]}
            if not config.scrape_stash_id:  # include only scenes without stash_id
                findScenes_params_excl['scene_filter']['stash_id'] = {'modifier': 'IS_NULL', 'value': 'none'}
            if not config.scrape_organized:  # include only scenes that are not organized
                findScenes_params_excl['scene_filter']['organized'] = False

            if len(required_tags) > 0:
                print("Getting Scenes Without Excluded Tags")
            scenes_without_tags = my_stash.findScenes(**findScenes_params_excl)
            scenes = scenes_without_tags

        if len(excluded_tags) == 0 and len(
                required_tags) == 0:  # If no tags are required or excluded
            findScenes_params_filtered = copy.deepcopy(findScenes_params)
            if not config.scrape_stash_id:  # include only scenes without stash_id
                findScenes_params_filtered['scene_filter']['stash_id'] = {'modifier': 'IS_NULL', 'value': 'none'}
            if not config.scrape_organized:  # include only scenes that are not organized
                findScenes_params_filtered['scene_filter']['organized'] = False
            scenes = my_stash.findScenes(**findScenes_params_filtered)

        if len(required_tags) > 0 and len(excluded_tags) > 0:
            scenes = [scene for scene in scenes_with_tags if scene in scenes_without_tags]  # Scenes that exist in both
        if not config.scrape_organized:
            print("Skipped Organized scenes")
        if not config.scrape_stash_id:
            print("Skipped scenes with a stash_id")
        print("Scenes to scrape", str(len(scenes)))

        for scene in scenes:
            scene['origpath'] = scene['path']
            scrapeScene(scene)

        print("Success! Finished.")
    except:
        logging.error("""Something went wrong.  Have you:
        • Checked to make sure you're running the "development" branch of Stash, not "latest"?
        • Checked that you can connect to Stash at the same IP and port listed in your configuration.py?
        If you've check both of these, run the script again with the --debug flag.  Then post the output of that in the Discord and hopefully someone can help.
        """, exc_info=config.debug_mode)


if __name__ == "__main__":
    main(sys.argv[1:])
