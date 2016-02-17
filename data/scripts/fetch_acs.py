#edited to work with python 2.7
import itertools, json, math, os, re, sys, urllib2, yaml

DEFAULT_OUTPUT_DIRECTORY = "outputs"
DEFAULT_OUTPUT_FILE = "acs_SLDU_data.json"
DEFAULT_OUTPUT_ACS_VARIABLES = False

def census_query(acs_variables, base_url, geo):
    """This function performs the Census API query. It returns hashes of the form
    {SLDU1 => {var1 => val1, var2 => val2, ...},
    {SLDU2 => {var1 => val1, var2 => val2, ...}, ... }"""

    acs_variables = list(acs_variables)
    SLDU = {}
    start_index = 0

    while start_index <= len(acs_variables):
        v = acs_variables[start_index:(start_index + 50)]
        url = "&".join([base_url, "get=" + ",".join(v), geo])
        print "Fetching {} fields from ACS...".format(len(v))
        print "URL: " + url
        # resp = json.loads(urllib.request.urlopen(url).read().decode("UTF-8"))
        resp = json.loads(urllib2.urlopen(url).read().decode("UTF-8"))
        # colnames, *data = resp
        colnames = resp[0]
        data = resp[1:]

        for row in data:
            r = dict(zip(colnames, row))

            # convert all values to ints except the geo identifiers
            for k in r.keys():
                if r[k] is not None and k not in ["state", "state legislative district (upper chamber)"]:
                    r[k] = int(r[k])

            # SLDU ids in the neighborhood file include the state and county number
            SLDU_id = "11" + r["state legislative district (upper chamber)"]
            if SLDU_id in SLDU:
                SLDU[SLDU_id].update(r)
            else:
                SLDU[SLDU_id] = r

        start_index += 50

    return SLDU


class ConfigurationException(Exception): pass


def main(args):
    config_file = args.pop(0) if args else "config.yaml"
    with open(config_file) as f:
        config = yaml.load(f.read())

    if "api" not in config: 
        raise ConfigurationException("No 'api' section in configuration")
    if "acs_year" not in config["api"]: 
        raise ConfigurationException("No ACS year specified")
    if "acs_period" not in config["api"]:
        raise ConfigurationException("No ACS period specified")

    acs_year = config["api"]["acs_year"]
    acs_period = config["api"]["acs_period"]

    if "acs_geography" not in config["api"]:
        raise ConfigurationException("No ACS geography specified")

    # construct the base URL

    base_url = "http://api.census.gov/data/{}/acs{}?".format(acs_year, acs_period)
    print "Using base URL: " + base_url

    # construct the geo parameter

    geo_for_key, geo_for_value = list(config["api"]["acs_geography"]["for"].items())[0]
    if re.match("all", geo_for_value, re.IGNORECASE):
        geo_for_value = "*"

    geo_for = "{}:{}".format(geo_for_key, geo_for_value)
    
    ll = list(config["api"]["acs_geography"]["in"].items())[0]
    geo_in = ":".join(str(x) for x in ll)

    geo = "for={}&in={}".format(geo_for, geo_in)

    # get the remaining configuration variables

    if "output" in config:
        output_directory = config["output"]["directory"] or DEFAULT_OUTPUT_DIRECTORY
        output_file = config["output"]["file"] or DEFAULT_OUTPUT_FILE
        output_acs_variables = config["output"]["acs_variables"] or DEFAULT_OUTPUT_ACS_VARIABLES
    else:
        output_directory = DEFAULT_OUTPUT_DIRECTORY
        output_file = DEFAULT_OUTPUT_FILE
        output_acs_variables = DEFAULT_OUTPUT_ACS_VARIABLES

    output_file = os.path.join(output_directory, output_file)
    print "Using output file: " + output_file

    for name in ["fields_rename", "fields_sum", "fields_sub", "fields_prod"]:
        if name not in config:
            print "WARNING: {} does not appear in configuration".format(name)

    fields_rename = config["fields_rename"] or {}
    fields_sum = config["fields_sum"] or {}
    fields_sub = config["fields_sub"] or {}
    fields_prod = config["fields_prod"] or {}

    # download all ACS variables and all corresponding margin-of-error variables
    # margin-of-error variables are obtained by replacing E by M in the ACS variable name

    all_acs_fields = set(fields_rename.values())
    all_acs_fields |= set(itertools.chain(*fields_sum.values()))
    all_acs_fields |= set(itertools.chain(*fields_sub.values()))
    all_acs_fields |= set(itertools.chain(*fields_prod.values()))

    all_acs_fields |= set(re.sub("E", "M", s) for s in all_acs_fields)
    SLDU = census_query(all_acs_fields, base_url, geo)

    # print all_acs_fields
    # print SLDU['11001000300']

    # compute named variables from ACS variables

    for SLDU_id, SLDU in SLDU.items():

        for outname, acsname in fields_rename.items():
            errname = re.sub("E", "M", acsname)
            SLDU[outname] = SLDU[acsname]
            SLDU[outname + "_margin"] = SLDU[errname]

        # # We found some Census documentation suggesting that we use the
        # # sqrt(sum of squares) for the margin of error for aggregate estimates.
        # # https://www.census.gov/acs/www/Downloads/data_documentation/Statistical_Testing/ACS_2008_Statistical_Testing.pdf

        for outname, acsfields in fields_sum.items():
            SLDU[outname] = 0
            SLDU[outname + "_margin"] = 0

            for acsname in acsfields:
                errname = re.sub("E", "M", acsname)
                SLDU[outname] += SLDU[acsname]
                SLDU[outname + "_margin"] += (SLDU[errname] / 1.645) ** 2

            SLDU[outname + "_margin"] = math.sqrt(SLDU[outname + "_margin"]) * 1.645

        for outname, acsfields in fields_sub.items():
            # first, *rest = acsfields
            first = acsfields[0]
            rest = acsfields[1:]

            errname = re.sub("E", "M", first)
            SLDU[outname] = SLDU[first]
            SLDU[outname + "_margin"] = (SLDU[errname] / 1.645) ** 2

            for acsname in rest:
                errname = re.sub("E", "M", acsname)
                SLDU[outname] -= SLDU[acsname]
                SLDU[outname + "_margin"] += (SLDU[errname] / 1.645) ** 2

            SLDU[outname + "_margin"] = math.sqrt(SLDU[outname + "_margin"]) * 1.645

        # TODO: compute margin of error for product field

        for outname, acsfields in fields_prod.items():
            SLDU[outname] = 1
            SLDU[outname + "_margin"] = 0

            for acsname in acsfields:
                try:
                    SLDU[outname] *= SLDU[acsname]
                except TypeError:
                    pass

    # remove ACS variables from output
    if not output_acs_variables:
        for SLDU in SLDU.values():
            for f in all_acs_fields:
                del(SLDU[f])

    # write the result to a JSON file
    with open(output_file, "w") as f:
        json.dump(SLDU, f, indent=2)


if __name__ == "__main__":
    main(sys.argv[1:])
