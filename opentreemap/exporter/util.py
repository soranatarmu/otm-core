# -*- coding: utf-8 -*-


def sanitize_unicode_value(value):
    # make sure every text value is of type 'str', coercing unicode
    if isinstance(value, str):
        return value
    elif isinstance(value, int):
        return str(value)
    else:
        return value.decode("utf-8")


# originally copied from, but now divergent from:
# https://github.com/azavea/django-queryset-csv/blob/
# master/djqscsv/djqscsv.py#L123
def sanitize_unicode_record(record):
    obj = type(record)()
    for key, val in record.items():
        if val:
            obj[sanitize_unicode_value(key)] = sanitize_unicode_value(val)

    return obj
