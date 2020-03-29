#!/usr/bin/env python

import logging
import argparse

from todoist.api import TodoistAPI

import time
import sys
from datetime import datetime

CHECKED = 1


def main():
    """Main process function."""
    parser = argparse.ArgumentParser()
    parser.add_argument('-a', '--api_key', help='Todoist API Key')
    parser.add_argument('-l', '--label', help='The next action label to use', default='next_action')
    parser.add_argument('-d', '--delay', help='Specify the delay in seconds between syncs', default=5, type=int)
    parser.add_argument('--debug', help='Enable debugging', action='store_true')
    parser.add_argument('--inbox', help='The method the Inbox project should be processed',
                        default='parallel', choices=['parallel', 'serial', 'none'])
    parser.add_argument('--parallel_suffix', default='.')
    parser.add_argument('--serial_suffix', default='_')
    parser.add_argument('--hide_future', help='Hide future dated next actions until the specified number of days',
                        default=7, type=int)
    parser.add_argument('--onetime', help='Update Todoist once and exit', action='store_true')
    parser.add_argument('--nocache', help='Disables caching data to disk for quicker syncing', action='store_true')
    args = parser.parse_args()

    # Set debug
    if args.debug:
        log_level = logging.DEBUG
    else:
        log_level = logging.INFO
    logging.basicConfig(level=log_level)

    # Check we have a API key
    if not args.api_key:
        logging.error('No API key set, exiting...')
        sys.exit(1)

    # Run the initial sync
    logging.debug('Connecting to the Todoist API')

    api_arguments = {'token': args.api_key}
    if args.nocache:
        logging.debug('Disabling local caching')
        api_arguments['cache'] = None

    api = TodoistAPI(**api_arguments)
    logging.debug('Syncing the current state from the API')
    api.sync()

    # Check the next action label exists
    labels = api.labels.all(lambda x: x['name'] == args.label)
    if len(labels) > 0:
        label_id = labels[0]['id']
        logging.debug('Label \'%s\' found as label id %d', args.label, label_id)
    else:
        logging.error("Label \'%s\' doesn't exist, please create it or change TODOIST_NEXT_ACTION_LABEL.", args.label)
        sys.exit(1)

    def get_project_type(project_object):
        """Identifies how a project should be handled."""
        name = project_object['name'].strip()
        if name == 'Inbox':
            if args.inbox == 'none':
                return None
            return args.inbox
        elif name[-1] == args.parallel_suffix:
            return 'parallel'
        elif name[-1] == args.serial_suffix:
            return 'serial'

    def get_item_type(item):
        """Identifies how a item with sub items should be handled."""
        name = item['content'].strip()
        if name[-1] == args.parallel_suffix:
            return 'parallel'
        elif name[-1] == args.serial_suffix:
            return 'serial'

    def add_label(item, label):
        if label not in item['labels']:
            labels = item['labels']
            logging.debug('Updating \'%s\' with label', item['content'])
            labels.append(label)
            api.items.update(item['id'], labels=labels)

    def remove_label(item, label):
        if label in item['labels']:
            labels = item['labels']
            logging.debug('Updating \'%s\' without label', item['content'])
            labels.remove(label)
            api.items.update(item['id'], labels=labels)

    # Main loop
    while True:
        try:
            api.sync()
        except Exception as e:
            logging.exception('Error trying to sync with Todoist API: %s' % str(e))
        else:
            for project in api.projects.all():
                project_type = get_project_type(project)
                if project_type:
                    logging.debug('Project \'%s\' being processed as %s', project['name'], project_type)

                    # Get all items for the project
                    items = sorted(api.items.all(lambda x: x['project_id'] == project['id']),
                                   key=lambda x: x['child_order'])
                    # filter for completable items
                    items = list(filter(lambda x: not x['content'].startswith('*'), items))

                    first_found = False

                    for item in items:

                        # If its too far in the future, remove the next_action tag and skip
                        if args.hide_future > 0 and 'due_date_utc' in item.data and item['due_date_utc'] is not None:
                            due_date = datetime.strptime(item['due_date_utc'], '%a %d %b %Y %H:%M:%S +0000')
                            future_diff = (due_date - datetime.utcnow()).total_seconds()
                            if future_diff >= (args.hide_future * 86400):
                                remove_label(item, label_id)
                                continue

                        if item['parent_id'] is None:
                            if project_type == 'serial':
                                if not first_found:
                                    add_label(item, label_id)
                                    first_found = True
                                else:
                                    remove_label(item, label_id)
                            elif project_type == 'parallel':
                                add_label(item, label_id)

                        item_type = get_item_type(item)
                        child_items = list(filter(lambda x: x['parent_id'] == item['id'], items))
                        if item_type:
                            logging.debug('Identified \'%s\' as %s type', item['content'], item_type)

                        if item_type or len(child_items) > 0:
                            if label_id in item['labels']:
                                # Process serial tagged items
                                if item_type == 'serial':
                                    child_first_found = False
                                    for child_item in child_items:
                                        if child_item['checked'] == 0 and not child_first_found:
                                            add_label(child_item, label_id)
                                            child_first_found = True
                                        else:
                                            remove_label(child_item, label_id)
                                # Process parallel tagged items or untagged parents
                                elif item_type == 'parallel':
                                    for child_item in child_items:
                                        add_label(child_item, label_id)

                                # Remove the label from the parent
                                remove_label(item, label_id)
                            else:
                                for child_item in child_items:
                                    remove_label(child_item, label_id)

            if len(api.queue):
                logging.debug('%d changes queued for sync... commiting to Todoist.', len(api.queue))
                api.commit()
            else:
                logging.debug('No changes queued, skipping sync.')

        # If onetime is set, exit after first execution.
        if args.onetime:
            break

        logging.debug('Sleeping for %d seconds', args.delay)
        time.sleep(args.delay)


if __name__ == '__main__':
    main()
