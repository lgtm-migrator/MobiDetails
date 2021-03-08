import re
import os.path
import urllib3
import certifi
import json
from flask import (
    Blueprint, flash, g, redirect, render_template, request, url_for, current_app as app
)
# from werkzeug.exceptions import abort
import psycopg2
import psycopg2.extras
# import tabix

# from MobiDetailsApp.auth import login_required
from MobiDetailsApp.db import get_db, close_db
from . import md_utilities


bp = Blueprint('md', __name__)
# to be modified when in prod - modify pythonpath and use venv with mod_wsgi
# https://stackoverflow.com/questions/10342114/how-to-set-pythonpath-on-web-server
# https://flask.palletsprojects.com/en/1.1.x/deploying/mod_wsgi/

# -------------------------------------------------------------------
# web app - index


@bp.route('/')
def index():
    # print(app.config['RUN_MODE'])
    db = get_db()
    curs = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    curs.execute(
        "SELECT COUNT(DISTINCT(name[1])) AS gene, COUNT(name) as transcript FROM gene WHERE variant_creation = 'ok'"
    )
    res = curs.fetchone()
    if res is None:
        error = "There is a problem with the number of genes."
        flash(error, 'w3-pale-red')
        md_utilities.send_error_email(
            md_utilities.prepare_email_html(
                'MobiDetails PostGreSQL error',
                '<p>There is a problem with the number of genes.<br /> in {}</p>'.format(os.path.basename(__file__))
            ),
            '[MobiDetails - PostGreSQL Error]'
        )
    else:
        close_db()
        return render_template('md/index.html', run_mode=md_utilities.get_running_mode(), nb_genes=res['gene'], nb_isoforms=res['transcript'])

# -------------------------------------------------------------------
# web app - about


@bp.route('/about')
def about():
    return render_template('md/about.html', run_mode=md_utilities.get_running_mode(), urls=md_utilities.urls, local_files=md_utilities.local_files, external_tools=md_utilities.external_tools)

# -------------------------------------------------------------------
# web app - changelog


@bp.route('/changelog')
def changelog():
    return render_template('md/changelog.html', run_mode=md_utilities.get_running_mode(), urls=md_utilities.urls)

# -------------------------------------------------------------------
# web app - gene


@bp.route('/gene/<string:gene_name>', methods=['GET', 'POST'])
def gene(gene_name=None):
    if gene_name is None:
        return render_template('md/unknown.html', query='No gene provided')
    elif re.search(r'[^\w-]', gene_name):
        return render_template('md/unknown.html', query=gene_name)
    db = get_db()
    curs = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    # main isoform? now canonical is stored in db
    # "SELECT * FROM gene WHERE name[1] = '{0}' AND number_of_exons = (SELECT MAX(number_of_exons) FROM gene WHERE name[1] = '{0}')".format(gene_name)
    curs.execute(
        "SELECT * FROM gene WHERE name[1] = %s AND canonical = 't'",
        (gene_name,)
    )
    main = curs.fetchone()
    if main is not None:
        curs.execute(
            "SELECT * FROM gene WHERE name[1] = %s ORDER BY number_of_exons DESC",
            (gene_name,)
        )  # get all isoforms
        result_all = curs.fetchall()
        num_iso = len(result_all)
        # in panelApp ?
        # we check  if the gene is in panelApp, if it is, we propose a link
        # https://panelapp.genomicsengland.co.uk/api/v1/genes/F91/
        http = urllib3.PoolManager(cert_reqs='CERT_REQUIRED', ca_certs=certifi.where())

        # get metadome json?
        enst_ver = {}
        # if not json metadome file on filesystem, create it in radboud server, then next time get it - it will then be available for future requests
        # if we have the main => next step
        if not os.path.isfile('{0}{1}.json'.format(md_utilities.local_files['metadome']['abs_path'], main['enst'])):
            for gene in result_all:
                if not os.path.isfile('{0}{1}.json'.format(md_utilities.local_files['metadome']['abs_path'], gene['enst'])):                    
                    if gene['enst'] not in enst_ver:
                        # get enst versions in a dict
                        metad_ts = None
                        try:
                            # print('{0}get_transcripts/{1}'.format(md_utilities.urls['metadome_api'], gene['name'][0]))
                            metad_ts = json.loads(
                                        http.request(
                                            'GET',
                                            '{0}get_transcripts/{1}'.format(
                                                md_utilities.urls['metadome_api'], gene['name'][0]
                                            )
                                        ).data.decode('utf-8')
                            )
                            if metad_ts is not None and \
                                    'trancript_ids' in metad_ts:
                                for ts in metad_ts['trancript_ids']:
                                    if ts['has_protein_data']:
                                        match_obj = re.search(r'^(ENST\d+)\.\d', ts['gencode_id'])
                                        enst_ver[match_obj.group(1)] = ts['gencode_id']
                        except Exception as e:
                            md_utilities.send_error_email(
                                md_utilities.prepare_email_html(
                                    'MobiDetails API error',
                                    '<p>MetaDome first block code failed for gene {0} ({1})<br /> - from {2} with args: {3}</p>'.format(
                                        gene_name,
                                        gene['enst'],
                                        os.path.basename(__file__),
                                        e.args
                                    )
                                ),
                                '[MobiDetails - API Error]'
                            )
                    break

        # we check if data exist at metadome
        # we have a set of metadome transcripts
        for enst in enst_ver:
            # print('enst: {}'.format(enst_ver[enst]))
            # print('--{}--'.format(app.debug))
            # print(json.dumps({'transcript_id': enst_ver[enst]}))
            if not os.path.isfile('{0}{1}.json'.format(md_utilities.local_files['metadome']['abs_path'], enst)):
                metad_data = None
                try:
                    metad_data = json.loads(
                                    http.request(
                                        'GET',
                                        '{0}status/{1}/'.format(
                                            md_utilities.urls['metadome_api'],
                                            enst_ver[enst])
                                    ).data.decode('utf-8')
                                )
                except Exception as e:
                    md_utilities.send_error_email(
                        md_utilities.prepare_email_html(
                            'MobiDetails API error',
                            '<p>MetaDome second block code failed for gene {0} ({1})<br /> - from {2} with args: {3}</p>'.format(
                                gene_name,
                                enst,
                                os.path.basename(__file__),
                                e.args
                            )
                        ),
                        '[MobiDetails - API Error]'
                    )
                if metad_data is not None:
                    if metad_data['status'] == 'PENDING':
                        # get transcript_ids ?? coz of the version number
                        # send request to build visualization to metadome
                        vis_request = None
                        # find out how to get app object
                        try:
                            # enst_metadome = {transcript_id: enst_ver[enst]}
                            header = md_utilities.api_agent
                            header['Content-Type'] = 'application/json'
                            vis_request = json.loads(
                                            http.request(
                                                'POST',
                                                '{0}submit_visualization/'.format(
                                                    md_utilities.urls['metadome_api']
                                                ),
                                                # headers={'Content-Type': 'application/json'},
                                                headers=header,
                                                body=json.dumps({'transcript_id': enst_ver[enst]})
                                            ).data.decode('utf-8')
                            )
                            if not app.debug:
                                app.logger.info('{} submitted to metadome'.format(vis_request['transcript_id']))
                            else:
                                print('{} submitted to metadome'.format(vis_request['transcript_id']))
                        except Exception as e:
                            md_utilities.send_error_email(
                                md_utilities.prepare_email_html(
                                    'MobiDetails API error',
                                    '<p>Error with metadome submission for {0} ({1})<br /> - from {2} with args: {3}</p>'.format(
                                        gene_name,
                                        enst,
                                        os.path.basename(__file__),
                                        e.args
                                    )
                                ),
                                '[MobiDetails - API Error]'
                            )
                            # print('Error with metadome submission for {}'.format(enst))
                    elif metad_data['status'] == 'SUCCESS':
                        # get_request = None
                        try:
                            get_request = json.loads(
                                            http.request(
                                                'GET',
                                                '{0}result/{1}/'.format(
                                                    md_utilities.urls['metadome_api'],
                                                    enst_ver[enst]
                                                )
                                            ).data.decode('utf-8')
                                        )
                            # copy in file system
                            with open('{0}{1}.json'.format(md_utilities.local_files['metadome']['abs_path'], enst), "w", encoding='utf-8') as metad_file:
                                json.dump(get_request, metad_file, ensure_ascii=False, indent=4)
                            if not app.debug:
                                app.logger.info('saving metadome {} into local file system'.format(enst_ver[enst]))
                            else:
                                print('saving metadome {} into local file system'.format(enst_ver[enst]))
                        except Exception as e:
                            md_utilities.send_error_email(
                                md_utilities.prepare_email_html(
                                    'MobiDetails API error',
                                    '<p>Error with metadome file writing for {0} ({1})<br /> - from {2} with args: {3}</p>'.format(
                                        gene_name,
                                        enst,
                                        os.path.basename(__file__),
                                        e.args
                                    )
                                ),
                                '[MobiDetails - API Error]'
                            )
                            # print('error saving metadome json file for {}'.format(enst))
        if result_all is not None:
            # get annotations
            curs.execute(
                "SELECT * FROM gene_annotation WHERE gene_name[1] = %s",
                (gene_name,)
            )
            annot = curs.fetchone()
            if annot is None:
                annot = {'nognomad': 'No values in gnomAD'}
            close_db()
            return render_template(
                'md/gene.html', run_mode=md_utilities.get_running_mode(), urls=md_utilities.urls, gene=gene_name,
                num_iso=num_iso, main_iso=main, res=result_all, annotations=annot
            )
        else:
            close_db()
            return render_template('md/unknown.html', run_mode=md_utilities.get_running_mode(), query=gene_name)
    else:
        close_db()
        return render_template('md/unknown.html', run_mode=md_utilities.get_running_mode(), query=gene_name)

# -------------------------------------------------------------------
# web app - all genes


@bp.route('/genes')
def genes():
    db = get_db()
    curs = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    curs.execute(
        "SELECT DISTINCT(name[1]) AS hgnc FROM gene ORDER BY name[1]"
    )
    genes = curs.fetchall()
    if genes:
        close_db()
        return render_template('md/genes.html', run_mode=md_utilities.get_running_mode(), genes=genes)
    else:
        close_db()
        return render_template('md/unknown.html', run_mode=md_utilities.get_running_mode())

# -------------------------------------------------------------------
# web app - variants in genes


@bp.route('/vars/<string:gene_name>', methods=['GET', 'POST'])
def vars(gene_name=None):
    if gene_name is None:
        return render_template('md/unknown.html', query='No gene provided')
    elif re.search(r'[^\w-]', gene_name):
        return render_template('md/unknown.html', query=gene_name)
    db = get_db()
    curs = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    # error = None
    # main isoform?
    curs.execute(
        "SELECT * FROM gene WHERE name[1] = %s AND canonical = 't'",
        (gene_name,)
    )
    main = curs.fetchone()
    if main is not None:
        curs.execute(
            "SELECT name, nm_version FROM gene WHERE name[1] = %s",
            (gene_name,)
        )  # get all isoforms
        result_all = curs.fetchall()
        num_iso = len(result_all)
        curs.execute(
            "SELECT *, a.id as vf_id, d.nm_version FROM variant_feature a, variant b, mobiuser c, gene d WHERE a.id = b.feature_id AND \
             a.creation_user = c.id  AND a.gene_name = d.name AND a.gene_name[1] = %s AND \
             b.genome_version = 'hg38'",
            (gene_name,)
        )
        variants = curs.fetchall()
        # if vars_type is not None:
        close_db()
        return render_template(
            'md/vars.html', run_mode=md_utilities.get_running_mode(), urls=md_utilities.urls, gene=gene_name,
            num_iso=num_iso, variants=variants, gene_info=main, res=result_all
        )
    else:
        close_db()
        return render_template('md/unknown.html', run_mode=md_utilities.get_running_mode(), query=gene_name)


# -------------------------------------------------------------------
# web app - variant


@bp.route('/variant/<int:variant_id>', methods=['GET', 'POST'])
def variant(variant_id=None):
    # below code is useles in flask
    # if variant_id is None:
    #     return render_template('unknown.html', query='No variant provided')
    # elif re.search(r'[^\d]', str(variant_id)):
    #     return render_template('unknown.html', query=variant_id)
    db = get_db()
    curs = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    # get all variant_features and gene info
    curs.execute(
        "SELECT *, a.id as var_id, c.id as mobiuser_id, d.so_accession FROM variant_feature a, gene b, mobiuser c, valid_prot_type d \
        WHERE a.gene_name = b.name AND a.creation_user = c.id AND a.prot_type = d.prot_type AND a.id = %s",
        (variant_id,)
    )
    variant_features = curs.fetchone()
    if variant_features is not None:
        # get variant info
        curs.execute(
            "SELECT * FROM variant WHERE feature_id = %s",
            (variant_id,)
        )
        variant = curs.fetchall()

        # length of c_name for priting on screen
        var_cname = variant_features['c_name']
        if len(var_cname) > 30:
            match_obj = re.search(r'(.+ins)[ATGC]+$', var_cname)
            if match_obj is not None:
                var_cname = match_obj.group(1)

        # dict for annotations
        annot = {}
        aa_pos = None
        # pos_splice_site = None
        domain = None
        # favourite var?
        curs.execute(
            "SELECT mobiuser_id FROM mobiuser_favourite WHERE feature_id = %s",
            (variant_id,)
        )
        favourite = curs.fetchone()
        if favourite is not None:
            favourite = True
        splicing_radar_labels = []
        splicing_radar_values = []
        for var in variant:
            if var['genome_version'] == 'hg38':
                # HGVS strict genomic names e.g. NC_000001.11:g.216422237G>A
                curs.execute(
                    "SELECT ncbi_name, genome_version FROM chromosomes WHERE name = %s",
                    (var['chr'],)
                )
                res_chr = curs.fetchall()
                for res in res_chr:
                    if res['genome_version'] == 'hg19':
                        annot['ncbi_chr_hg19'] = res['ncbi_name']
                    elif res['genome_version'] == 'hg38':
                        annot['ncbi_chr_hg38'] = res['ncbi_name']
                # compute position / splice sites
                if variant_features['variant_size'] < 50 and \
                        variant_features['start_segment_type'] == 'exon':  # and \
                    # variant_features['start_segment_type'] == variant_features['end_segment_type'] and \
                    # variant_features['start_segment_number'] == variant_features['end_segment_number']: # and \
                    # not re.search(r'\*', variant_features['c_name']) and \
                    # not re.search(r'^-', variant_features['c_name']):
                    curs.execute(
                        "SELECT * FROM segment WHERE genome_version = %s\
                        AND gene_name[1] = %s and gene_name[2] = %s AND type = 'exon' AND number = %s",
                        (var['genome_version'], variant_features['gene_name'][0], variant_features['gene_name'][1], variant_features['start_segment_number'])
                    )
                    positions = curs.fetchone()
                    # get info to build hexoSplice link
                    if variant_features['dna_type'] == 'substitution':
                        annot['exon_sequence'] = md_utilities.get_exon_sequence(positions, var['chr'], variant_features['strand'])
                        annot['exon_first_nt_cdna_position'] = md_utilities.get_exon_first_nt_cdna_position(positions, var['pos'], variant_features['c_name'])
                        # annot['hexosplice_link'] = '{0}results.php?refSeq={1}&amp;variation=c.{2}&amp;c={3}'.format(md_utilities.urls['hexosplice'], exon_sequence, variant_features['c_name'], exon_first_nt_cdna_position)
                    # if not re.search(r'\*', variant_features['c_name']) and \
                    #        not re.search(r'^-', variant_features['c_name']):
                    # if not re.search(r'^[\*-]', variant_features['c_name']):
                    # get a tuple ['site_type', 'dist(bp)']
                    (annot['nearest_site_type'], annot['nearest_site_dist']) = md_utilities.get_pos_splice_site(
                        var['pos'], positions
                    )
                    # relative position in exon for canvas drawing
                    # get a tuple ['relative position in exon canvas', 'segment_size']
                    (annot['pos_exon_canvas'], annot['segment_size']) = md_utilities.get_pos_exon_canvas(
                        var['pos'], positions
                    )
                    # get neighbours type, number
                    (annot['preceeding_segment_type'], annot['preceeding_segment_number'],
                        annot['following_segment_type'], annot['following_segment_number']) = md_utilities.get_exon_neighbours(db, positions)
                    # print('preceeding: {0}{1};following: {2}{3}'.format(
                    #    annot['preceeding_segment_type'], annot['preceeding_segment_number'],
                    #    annot['following_segment_type'], annot['following_segment_number'])
                    # )
                    # get natural ss maxent scores
                    if annot['preceeding_segment_number'] != 'UTR':
                        (annot['nat3ss_score'], annot['nat3ss_seq']) = md_utilities.get_maxent_natural_sites_scores(
                            var['chr'], variant_features['strand'], 3, positions
                        )
                    if annot['following_segment_number'] != 'UTR':
                        (annot['nat5ss_score'], annot['nat5ss_seq']) = md_utilities.get_maxent_natural_sites_scores(
                            var['chr'], variant_features['strand'], 5, positions
                        )
                    # variants beginning in exon and finishing in intron
                    # we don't treat them as this is obvious
                    # if variant_features['start_segment_type'] != variant_features['end_segment_type'] or \
                    # variant_features['start_segment_number'] != variant_features['end_segment_number']:
                    #   indels > 1 bp
                    #   get a tuple ['site_type', 'dist(bp)']
                    #   pos_splice_site_second = md_utilities.get_pos_splice_site(
                    #               db, var['pos'], variant_features['end_segment_type'], variant_features['end_segment_number'], variant_features['gene_name'])
                    #   if pos_splice_site[1] > pos_splice_site_second[1]:
                    #       pos_splice_site_second nearest from splice site
                    #       print('{0}-{1}'.format(pos_splice_site, pos_splice_site_second))
                    #       pos_splice_site = pos_splice_site_second
                    # compute position in domain
                    # 1st get aa pos
                    if variant_features['prot_type'] != 'unknown':
                        aa_pos = md_utilities.get_aa_position(variant_features['p_name'])
                        curs.execute(
                            "SELECT * FROM protein_domain WHERE gene_name[2] = '{0}' AND (('{1}' \
                            BETWEEN aa_start AND aa_end) OR ('{2}' BETWEEN aa_start AND aa_end));".format(
                                variant_features['gene_name'][1], aa_pos[0], aa_pos[1]
                            )
                        )
                        domain = curs.fetchall()
                        # metadome data?
                        if variant_features['dna_type'] == 'substitution' and \
                                os.path.isfile('{0}{1}.json'.format(md_utilities.local_files['metadome']['abs_path'], variant_features['enst'])) is True:
                            # get value in json file
                            with open('{0}{1}.json'.format(md_utilities.local_files['metadome']['abs_path'], variant_features['enst']), "r") as metad_file:
                                metad_json = json.load(metad_file)
                                if 'positional_annotation' in metad_json:
                                    for pos in metad_json['positional_annotation']:
                                        if int(pos['protein_pos']) == int(aa_pos[0]):
                                            if 'sw_dn_ds' in pos:
                                                annot['metadome_dn_ds'] = "{:.2f}".format(float(pos['sw_dn_ds']))
                                                [annot['metadome_effect'], annot['metadome_color']] = md_utilities.get_metadome_colors(annot['metadome_dn_ds'])
                                if 'transcript_id' in metad_json:
                                    annot['metadome_transcript'] = metad_json['transcript_id']
                if variant_features['start_segment_type'] == 'intron':
                    annot['dist_from_exon'], sign = md_utilities.get_pos_splice_site_intron(variant_features['c_name'])
                    if variant_features['dna_type'] == 'substitution':
                        annot['substitution_nature'] = md_utilities.get_substitution_nature(variant_features['c_name'])
                # MPA indel splice
                elif variant_features['start_segment_type'] == 'intron' and \
                        (variant_features['dna_type'] == 'indel' or
                            variant_features['dna_type'] == 'deletion' or
                            variant_features['dna_type'] == 'duplication') and \
                        variant_features['variant_size'] < 50:
                    if annot['dist_from_exon'] <= 20 and \
                            ('mpa_score' not in annot or annot['mpa_score'] < 6):
                        annot['mpa_score'] = 6
                        annot['mpa_impact'] = 'splice indel'
                # intronic variant canvas
                if variant_features['start_segment_type'] == 'intron' and \
                        annot['dist_from_exon'] <= 100 and \
                        variant_features['variant_size'] < 50:
                    annot['pos_intron_canvas'] = 200 - annot['dist_from_exon']  # relative position inside canvas fomr exon beginning
                    annot['neighb_exon_number'] = variant_features['start_segment_number'] + 1
                    if sign == '+':
                        annot['neighb_exon_number'] = variant_features['start_segment_number']
                        annot['pos_intron_canvas'] = 400 + annot['dist_from_exon']  # relative position inside canvas from exon end

                    annot['pos_exon_canvas'] = None
                    # get info from neighboring exon
                    curs.execute(
                        "SELECT * FROM segment WHERE genome_version = %s\
                        AND gene_name[1] = %s and gene_name[2] = %s AND type = 'exon' AND number = %s",
                        (var['genome_version'], variant_features['gene_name'][0], variant_features['gene_name'][1], annot['neighb_exon_number'])
                    )
                    positions_neighb_exon = curs.fetchone()
                    if sign == '+':
                        annot['preceeding_segment_type'] = None
                        annot['preceeding_segment_number'] = None
                        annot['following_segment_type'] = 'intron'
                        annot['following_segment_number'] = variant_features['start_segment_number']
                        (annot['nat5ss_score'], annot['nat5ss_seq']) = md_utilities.get_maxent_natural_sites_scores(
                            var['chr'], variant_features['strand'], 5, positions_neighb_exon
                        )
                    else:
                        annot['preceeding_segment_type'] = 'intron'
                        annot['preceeding_segment_number'] = variant_features['start_segment_number']
                        annot['following_segment_type'] = None
                        annot['following_segment_number'] = None
                        (annot['nat3ss_score'], annot['nat3ss_seq']) = md_utilities.get_maxent_natural_sites_scores(
                            var['chr'], variant_features['strand'], 3, positions_neighb_exon
                        )
                # clinvar
                record = md_utilities.get_value_from_tabix_file('Clinvar', md_utilities.local_files['clinvar_hg38']['abs_path'], var, variant_features)
                if isinstance(record, str):
                    annot['clinsig'] = "{0} {1}".format(record, md_utilities.external_tools['ClinVar']['version'])
                else:
                    annot['clinvar_id'] = record[2]
                    match_object = re.search(r'CLNSIG=(.+);CLNVC=', record[7])
                    if match_object:
                        match2_object = re.search(r'^(.+);CLNSIGCONF=(.+)$', match_object.group(1))
                        if match2_object:
                            annot['clinsig'] = match2_object.group(1)
                            annot['clinsigconf'] = match2_object.group(2)
                            annot['clinsigconf'] = annot['clinsigconf'].replace('%3B', '-')
                        else:
                            annot['clinsig'] = match_object.group(1)
                    elif re.search(r'CLNREVSTAT=no_interpretation_for_the_single_variant', record[7]):
                        annot['clinsig'] = 'No interpretation for the single variant'
                    if 'clinsig' in annot and \
                            re.search('pathogenic', annot['clinsig'], re.IGNORECASE) and \
                            not re.search('pathogenicity', annot['clinsig'], re.IGNORECASE):
                        annot['mpa_score'] = 10
                        annot['mpa_impact'] = 'clinvar pathogenic'
                # MPA PTC
                if 'mpa_score' not in annot or \
                        annot['mpa_impact'] != 'clinvar pathogenic':
                    if variant_features['prot_type'] == 'nonsense' or \
                            variant_features['prot_type'] == 'frameshift':
                        annot['mpa_score'] = 10
                        annot['mpa_impact'] = variant_features['prot_type']
                # gnomadv3
                # record = md_utilities.get_value_from_tabix_file('gnomADv3', md_utilities.local_files['gnomad_3']['abs_path'], var, variant_features)
                # if isinstance(record, str):
                #     annot['gnomadv3'] = record
                # else:
                #     match_obj = re.search(r'AF=([\d\.e-]+);', record[7])
                #     if match_obj:
                #         annot['gnomadv3'] = match_obj.group(1)
                # gnomadv3 annovar style
                record = md_utilities.get_value_from_tabix_file('gnomADv3', md_utilities.local_files['gnomad_3']['abs_path'], var, variant_features)
                if isinstance(record, str):
                    annot['gnomadv3'] = record
                else:
                    # print(record)
                    annot['gnomadv3'] = record[int(md_utilities.external_tools['gnomAD']['annovar_format_af_col'])]
                # dbNSFP
                # # Eigen from dbNSFP for coding variants                
                # if variant_features['dna_type'] == 'substitution' and \
                #         re.search(r'^[^\*-]', variant_features['c_name']) and \
                #         variant_features['start_segment_type'] == 'exon':
                #     record = md_utilities.get_value_from_tabix_file('dbnsfp', md_utilities.local_files['dbnsfp']['abs_path'], var, variant_features)
                #     try:
                #         annot['eigen_raw'] = format(float(record[int(md_utilities.external_tools['Eigen']['dbNSFP_value_col'])]), '.2f')
                #         annot['eigen_phred'] = format(float(record[int(md_utilities.external_tools['Eigen']['dbNSFP_pred_col'])]), '.2f')
                #         # annot['eigen_raw'] = format(float(record[113]), '.2f')
                #         # annot['eigen_phred'] = format(float(record[115]), '.2f')
                #     except Exception:
                #         annot['eigen'] = 'No match in dbNSFP for Eigen'
                #     if 'eigen_raw' in annot and \
                #             annot['eigen_raw'] == '.':
                #         annot['eigen'] = 'No score in dbNSFP for Eigen'
                if variant_features['prot_type'] == 'missense':
                    # CADD
                    record = md_utilities.get_value_from_tabix_file('dbnsfp', md_utilities.local_files['dbnsfp']['abs_path'], var, variant_features)
                    # print(record)
                    try:
                        annot['cadd_raw'] = format(float(record[int(md_utilities.external_tools['CADD']['dbNSFP_value_col'])]), '.2f')
                        annot['cadd_phred'] = format(float(record[int(md_utilities.external_tools['CADD']['dbNSFP_phred_col'])]), '.2f')
                        # annot['eigen_raw'] = format(float(record[113]), '.2f')
                        # annot['eigen_phred'] = format(float(record[115]), '.2f')
                    except Exception:
                        annot['cadd'] = 'No match in dbNSFP for CADD'
                    if 'cadd_raw' in annot and \
                            annot['cadd_raw'] == '.':
                        annot['cadd'] = 'No score in dbNSFP for CADD'
                    # Eigen
                    # record = md_utilities.get_value_from_tabix_file('dbnsfp', md_utilities.local_files['dbnsfp']['abs_path'], var, variant_features)
                    try:
                        annot['eigen_raw'] = format(float(record[int(md_utilities.external_tools['Eigen']['dbNSFP_value_col'])]), '.2f')
                        annot['eigen_phred'] = format(float(record[int(md_utilities.external_tools['Eigen']['dbNSFP_pred_col'])]), '.2f')
                        # annot['eigen_raw'] = format(float(record[113]), '.2f')
                        # annot['eigen_phred'] = format(float(record[115]), '.2f')
                    except Exception:
                        annot['eigen'] = 'No match in dbNSFP for Eigen'
                    if 'eigen_raw' in annot and \
                            annot['eigen_raw'] == '.':
                        annot['eigen'] = 'No score in dbNSFP for Eigen'
                    # record = md_utilities.get_value_from_tabix_file('dbnsfp', md_utilities.local_files['dbnsfp']['abs_path'], var, variant_features)
                    # record comes from Eigen section above
                    if isinstance(record, str):
                        annot['dbnsfp'] = "{0} {1}".format(record, md_utilities.external_tools['dbNSFP']['version'])
                    else:
                        # first: get enst we're dealing with
                        i = 0
                        transcript_index = 0
                        enst_list = re.split(';', record[14])
                        if len(enst_list) > 1:
                            for enst in enst_list:
                                if variant_features['enst'] == enst:
                                    transcript_index = i
                                i += 1
                        # print(transcript_index)
                        # then iterate for each score of interest, e.g.  sift..
                        # missense:
                        # mpa score
                        mpa_missense = 0
                        mpa_avail = 0
                        # sift
                        annot['sift_score'], annot['sift_pred'], annot['sift_star'] = md_utilities.getdbNSFP_results(
                            transcript_index, int(md_utilities.external_tools['SIFT']['dbNSFP_value_col']), int(md_utilities.external_tools['SIFT']['dbNSFP_pred_col']), ';', 'basic', 1.1, 'lt', record
                        )
                        # annot['sift_score'], annot['sift_pred'], annot['sift_star'] = md_utilities.getdbNSFP_results(
                        #     transcript_index, 36, 38, ';', 'basic', 1.1, 'lt', record
                        # )
                        
                        annot['sift_color'] = md_utilities.get_preditor_single_threshold_color(annot['sift_score'], 'sift')
                        if annot['sift_pred'] == 'Damaging':
                            mpa_missense += 1
                        if annot['sift_pred'] != 'no prediction':
                            mpa_avail += 1
                        # polyphen 2 hdiv
                        annot['pph2_hdiv_score'], annot['pph2_hdiv_pred'], annot['pph2_hdiv_star'] = md_utilities.getdbNSFP_results(
                            transcript_index, int(md_utilities.external_tools['Polyphen-2']['dbNSFP_value_col_hdiv']), int(md_utilities.external_tools['Polyphen-2']['dbNSFP_pred_col_hdiv']), ';', 'pph2', -0.1, 'gt', record
                        )
                        #  annot['pph2_hdiv_score'], annot['pph2_hdiv_pred'], annot['pph2_hdiv_star'] = md_utilities.getdbNSFP_results(
                        #     transcript_index, 42, 44, ';', 'pph2', -0.1, 'gt', record
                        # )

                        annot['pph2_hdiv_color'] = md_utilities.get_preditor_double_threshold_color(annot['pph2_hdiv_score'], 'pph2_hdiv_mid', 'pph2_hdiv_max')
                        if re.search('Damaging', annot['pph2_hdiv_pred']):
                            mpa_missense += 1
                        if annot['pph2_hdiv_pred'] != 'no prediction':
                            mpa_avail += 1
                        # hvar
                        annot['pph2_hvar_score'], annot['pph2_hvar_pred'], annot['pph2_hvar_star'] = md_utilities.getdbNSFP_results(
                            transcript_index, int(md_utilities.external_tools['Polyphen-2']['dbNSFP_value_col_hvar']), int(md_utilities.external_tools['Polyphen-2']['dbNSFP_pred_col_hvar']), ';', 'pph2', -0.1, 'gt', record
                        )
                        # annot['pph2_hvar_score'], annot['pph2_hvar_pred'], annot['pph2_hvar_star'] = md_utilities.getdbNSFP_results(
                        #     transcript_index, 45, 47, ';', 'pph2', -0.1, 'gt', record
                        # )

                        annot['pph2_hvar_color'] = md_utilities.get_preditor_double_threshold_color(annot['pph2_hvar_score'], 'pph2_hvar_mid', 'pph2_hvar_max')
                        if re.search('Damaging', annot['pph2_hvar_pred']):
                            mpa_missense += 1
                        if annot['pph2_hvar_pred'] != 'no prediction':
                            mpa_avail += 1
                        # fathmm
                        annot['fathmm_score'], annot['fathmm_pred'], annot['fathmm_star'] = md_utilities.getdbNSFP_results(
                            transcript_index, int(md_utilities.external_tools['FatHMM']['dbNSFP_value_col']), int(md_utilities.external_tools['FatHMM']['dbNSFP_pred_col']), ';', 'basic', 20, 'lt', record
                        )
                        # annot['fathmm_score'], annot['fathmm_pred'], annot['fathmm_star'] = md_utilities.getdbNSFP_results(
                        #     transcript_index, 60, 62, ';', 'basic', 20, 'lt', record
                        # )

                        annot['fathmm_color'] = md_utilities.get_preditor_single_threshold_reverted_color(annot['fathmm_score'], 'fathmm')
                        if annot['fathmm_pred'] == 'Damaging':
                            mpa_missense += 1
                        if annot['fathmm_pred'] != 'no prediction':
                            mpa_avail += 1
                        # fathmm-mkl -- not displayed
                        annot['fathmm_mkl_score'], annot['fathmm_mkl_pred'], annot['fathmm_mkl_star'] = md_utilities.getdbNSFP_results(
                            transcript_index, int(md_utilities.hidden_external_tools['FatHMM-MKL']['dbNSFP_value_col']), int(md_utilities.hidden_external_tools['FatHMM-MKL']['dbNSFP_pred_col']), ';', 'basic', 20, 'lt', record
                        )
                        # annot['fathmm_mkl_score'], annot['fathmm_mkl_pred'], annot['fathmm_mkl_star'] = md_utilities.getdbNSFP_results(
                        #     transcript_index, 106, 108, ';', 'basic', 20, 'lt', record
                        # )

                        annot['fathmm_mkl_color'] = md_utilities.get_preditor_single_threshold_reverted_color(annot['fathmm_mkl_score'], 'fathmm-mkl')
                        if annot['fathmm_mkl_pred'] == 'Damaging':
                            mpa_missense += 1
                        if annot['fathmm_mkl_pred'] != 'no prediction':
                            mpa_avail += 1
                        # provean -- not displayed
                        annot['provean_score'], annot['provean_pred'], annot['provean_star'] = md_utilities.getdbNSFP_results(
                            transcript_index, int(md_utilities.hidden_external_tools['Provean']['dbNSFP_value_col']), int(md_utilities.hidden_external_tools['Provean']['dbNSFP_pred_col']), ';', 'basic', 20, 'lt', record
                        )
                        # annot['provean_score'], annot['provean_pred'], annot['provean_star'] = md_utilities.getdbNSFP_results(
                        #     transcript_index, 63, 65, ';', 'basic', 20, 'lt', record
                        # )
                        # annot['provean_color'] = md_utilities.get_preditor_single_threshold_reverted_color(annot['provean_score'], 'provean')
                        # print(re.split(';', record[65])[i])
                        if annot['provean_pred'] == 'Damaging':
                            mpa_missense += 1
                        if annot['provean_pred'] != 'no prediction':
                            mpa_avail += 1
                        # LRT -- not displayed
                        annot['lrt_score'], annot['lrt_pred'], annot['lrt_star'] = md_utilities.getdbNSFP_results(
                            transcript_index, int(md_utilities.hidden_external_tools['LRT']['dbNSFP_value_col']), int(md_utilities.hidden_external_tools['LRT']['dbNSFP_pred_col']), ';', 'basic', -1, 'gt', record
                        )
                        # annot['lrt_score'], annot['lrt_pred'], annot['lrt_star'] = md_utilities.getdbNSFP_results(
                        #     transcript_index, 48, 50, ';', 'basic', -1, 'gt', record
                        # )

                        if annot['lrt_pred'] == 'Damaging':
                            mpa_missense += 1
                        if re.search(r'^[DUN]', annot['lrt_pred']):
                            mpa_avail += 1
                        # MutationTaster -- not displayed
                        annot['mt_score'], annot['mt_pred'], annot['mt_star'] = md_utilities.getdbNSFP_results(
                            transcript_index, int(md_utilities.hidden_external_tools['MutationTaster']['dbNSFP_value_col']), int(md_utilities.hidden_external_tools['MutationTaster']['dbNSFP_pred_col']), ';', 'mt', -1, 'gt', record
                        )
                        # annot['mt_score'], annot['mt_pred'], annot['mt_star'] = md_utilities.getdbNSFP_results(
                        #     transcript_index, 52, 54, ';', 'mt', -1, 'gt', record
                        # )
                        # print('{}-{}-{}-{}'.format(annot['fathmm_mkl_score'], annot['provean_score'], annot['lrt_score'], annot['mt_score']))

                        if re.search('Disease causing', annot['mt_pred']):
                            mpa_missense += 1
                        if annot['mt_pred'] != 'no prediction':
                            mpa_avail += 1
                        # ClinPred
                        annot['clinpred_score'], annot['clinpred_pred'], annot['clinpred_star'] = md_utilities.getdbNSFP_results(
                            transcript_index, int(md_utilities.external_tools['ClinPred']['dbNSFP_value_col']), int(md_utilities.external_tools['ClinPred']['dbNSFP_pred_col']), ';', 'basic', '-1', 'gt', record
                        )
                        # clinpred score in dbNSFP, contrary to other scores, presents with 9-10 numbers after '.'
                        try:
                            annot['clinpred_score'] = format(float(annot['clinpred_score']), '.3f')
                        except Exception:
                            pass
                            
                        annot['clinpred_color'] = md_utilities.get_preditor_single_threshold_color(annot['clinpred_score'], 'clinpred')
                        
                        # REVEL
                        annot['revel_score'], annot['revel_pred'], annot['revel_star'] = md_utilities.getdbNSFP_results(
                            transcript_index, int(md_utilities.external_tools['REVEL']['dbNSFP_value_col']), int(md_utilities.external_tools['REVEL']['dbNSFP_pred_col']), ';', 'basic', '-1', 'gt', record
                        )
                        
                        # annot['revel_score'], annot['revel_pred'], annot['revel_star'] = md_utilities.getdbNSFP_results(
                        #     transcript_index, 78, 78, ';', 'basic', '-1', 'gt', record
                        # )
                        # no REVEL pred in dbNSFP => custom
                        if annot['revel_score'] != '.' and \
                                float(annot['revel_score']) < 0.2:
                            annot['revel_pred'] = md_utilities.predictors_translations['revel']['B']
                        elif annot['revel_score'] != '.' and \
                                float(annot['revel_score']) > 0.5:
                            annot['revel_pred'] = md_utilities.predictors_translations['revel']['D']
                        elif annot['revel_score'] != '.':
                            annot['revel_pred'] = md_utilities.predictors_translations['revel']['U']
                        else:
                            annot['revel_pred'] = 'no prediction'

                        #   annot['revel_pred'] = 'No interpretation'
                        annot['revel_color'] = md_utilities.get_preditor_double_threshold_color(annot['revel_score'], 'revel_min', 'revel_max')

                        # meta SVM
                        # print(record[68])
                        # annot['msvm_score'] = record[68]
                        annot['msvm_score'] = record[int(md_utilities.external_tools['MetaSVM-LR']['dbNSFP_value_col_msvm'])]
                        annot['msvm_color'] = md_utilities.get_preditor_single_threshold_color(annot['msvm_score'], 'meta-svm')
                        annot['msvm_pred'] = md_utilities.predictors_translations['basic'][record[int(md_utilities.external_tools['MetaSVM-LR']['dbNSFP_value_pred_msvm'])]]
                        # annot['msvm_pred'] = md_utilities.predictors_translations['basic'][record[70]]
                        if annot['msvm_pred'] == 'Damaging':
                            mpa_missense += 1
                        if annot['msvm_pred'] != 'no prediction':
                            mpa_avail += 1
                        # meta LR
                        # annot['mlr_score'] = record[71]
                        annot['mlr_score'] = record[int(md_utilities.external_tools['MetaSVM-LR']['dbNSFP_value_col_mlr'])]
                        annot['mlr_color'] = md_utilities.get_preditor_single_threshold_color(annot['mlr_score'], 'meta-lr')
                        annot['mlr_pred'] = md_utilities.predictors_translations['basic'][record[int(md_utilities.external_tools['MetaSVM-LR']['dbNSFP_value_pred_mlr'])]]
                        # annot['mlr_pred'] = md_utilities.predictors_translations['basic'][record[73]]
                        if annot['mlr_pred'] == 'Damaging':
                            mpa_missense += 1
                        if annot['mlr_pred'] != 'no prediction':
                            mpa_avail += 1
                        annot['m_rel'] = record[int(md_utilities.external_tools['MetaSVM-LR']['dbNSFP_value_col_mrel'])]  # reliability index for meta score (1-10): the higher, the higher the reliability
                        # annot['m_rel'] = record[74]
                        # print('mpa_avail: {}'.format(mpa_avail))
                        if (('mpa_score' not in annot or
                                annot['mpa_score'] < mpa_missense) and
                                mpa_avail > 0):
                            # print('{0}/{1}'.format(mpa_missense, mpa_avail))
                            annot['mpa_score'] = float('{0:.2f}'.format((mpa_missense / mpa_avail) * 10))
                            if annot['mpa_score'] >= 8:
                                annot['mpa_impact'] = 'high missense'
                            elif annot['mpa_score'] >= 6:
                                annot['mpa_impact'] = 'moderate missense'
                            else:
                                annot['mpa_impact'] = 'low missense'
                # dbMTS
                if variant_features['dna_type'] == 'substitution' and \
                        re.search(r'^\*', variant_features['c_name']):
                    record = md_utilities.get_value_from_tabix_file('dbmts', md_utilities.local_files['dbmts']['abs_path'], var, variant_features)
                    if isinstance(record, str):
                        annot['dbmts'] = "{0} {1}".format(record, md_utilities.external_tools['dbMTS']['version'])
                    else:
                        # Eigen from dbMTS for 3'UTR variants 
                        try:
                            annot['eigen_raw'] = format(float(record[int(md_utilities.external_tools['Eigen']['dbMTS_value_col'])]), '.2f')
                            annot['eigen_phred'] = format(float(record[int(md_utilities.external_tools['Eigen']['dbMTS_pred_col'])]), '.2f')
                            # annot['eigen_raw'] = format(float(record[127]), '.2f')
                            # annot['eigen_phred'] = format(float(record[128]), '.2f')
                        except Exception:
                            annot['eigen'] = 'No match in dbMTS for Eigen'
                        if 'eigen_raw' in annot and \
                                annot['eigen_raw'] == '.':
                            annot['eigen'] = 'No score in dbMTS for Eigen'
                        try:
                            # Miranda
                            annot['miranda_cat'] = record[int(md_utilities.external_tools['dbMTS']['miranda_cat_col'])]
                            annot['miranda_rankscore'] = record[int(md_utilities.external_tools['dbMTS']['miranda_rankscore_col'])]
                            annot['miranda_maxdiff'] = record[int(md_utilities.external_tools['dbMTS']['miranda_maxdiff_col'])]
                            annot['miranda_refbestmir'] = md_utilities.format_mirs(record[int(md_utilities.external_tools['dbMTS']['miranda_refbestmir_col'])])
                            # annot['miranda_refbestmir'] = record[139].replace(';', '<br />')
                            annot['miranda_refbestscore'] = record[int(md_utilities.external_tools['dbMTS']['miranda_refbestscore_col'])]
                            annot['miranda_altbestmir'] = md_utilities.format_mirs(record[int(md_utilities.external_tools['dbMTS']['miranda_altbestmir_col'])])
                            annot['miranda_altbestscore'] = record[int(md_utilities.external_tools['dbMTS']['miranda_altbestscore_col'])]
                            # TargetScan
                            annot['targetscan_cat'] = record[int(md_utilities.external_tools['dbMTS']['targetscan_cat_col'])]
                            annot['targetscan_rankscore'] = record[int(md_utilities.external_tools['dbMTS']['targetscan_rankscore_col'])]
                            annot['targetscan_maxdiff'] = record[int(md_utilities.external_tools['dbMTS']['targetscan_maxdiff_col'])]
                            annot['targetscan_refbestmir'] = md_utilities.format_mirs(record[int(md_utilities.external_tools['dbMTS']['targetscan_refbestmir_col'])])
                            annot['targetscan_refbestscore'] = record[int(md_utilities.external_tools['dbMTS']['targetscan_refbestscore_col'])]
                            annot['targetscan_altbestmir'] = md_utilities.format_mirs(record[int(md_utilities.external_tools['dbMTS']['targetscan_altbestmir_col'])])
                            annot['targetscan_altbestscore'] = record[int(md_utilities.external_tools['dbMTS']['targetscan_altbestscore_col'])]
                            # RNAHybrid
                            annot['rnahybrid_cat'] = record[int(md_utilities.external_tools['dbMTS']['rnahybrid_cat_col'])]
                            annot['rnahybrid_rankscore'] = record[int(md_utilities.external_tools['dbMTS']['rnahybrid_rankscore_col'])]
                            annot['rnahybrid_maxdiff'] = record[int(md_utilities.external_tools['dbMTS']['rnahybrid_maxdiff_col'])]
                            annot['rnahybrid_refbestmir'] = md_utilities.format_mirs(record[int(md_utilities.external_tools['dbMTS']['rnahybrid_refbestmir_col'])])
                            annot['rnahybrid_refbestscore'] = record[int(md_utilities.external_tools['dbMTS']['rnahybrid_refbestscore_col'])]
                            annot['rnahybrid_altbestmir'] = md_utilities.format_mirs(record[int(md_utilities.external_tools['dbMTS']['rnahybrid_altbestmir_col'])])
                            annot['rnahybrid_altbestscore'] = record[int(md_utilities.external_tools['dbMTS']['rnahybrid_altbestscore_col'])]
                            # # Miranda
                            # annot['miranda_cat'] = record[160]
                            # annot['miranda_rankscore'] = record[158]
                            # annot['miranda_maxdiff'] = record[157]
                            # annot['miranda_refbestmir'] = md_utilities.format_mirs(record[139])
                            # # annot['miranda_refbestmir'] = record[139].replace(';', '<br />')
                            # annot['miranda_refbestscore'] = record[138]
                            # annot['miranda_altbestmir'] = md_utilities.format_mirs(record[152])
                            # annot['miranda_altbestscore'] = record[151]
                            # # TargetScan
                            # annot['targetscan_cat'] = record[190]
                            # annot['targetscan_rankscore'] = record[188]
                            # annot['targetscan_maxdiff'] = record[187]
                            # annot['targetscan_refbestmir'] = md_utilities.format_mirs(record[169])
                            # annot['targetscan_refbestscore'] = record[168]
                            # annot['targetscan_altbestmir'] = md_utilities.format_mirs(record[182])
                            # annot['targetscan_altbestscore'] = record[181]
                            # # RNAHybrid
                            # annot['rnahybrid_cat'] = record[220]
                            # annot['rnahybrid_rankscore'] = record[218]
                            # annot['rnahybrid_maxdiff'] = record[217]
                            # annot['rnahybrid_refbestmir'] = md_utilities.format_mirs(record[199])
                            # annot['rnahybrid_refbestscore'] = record[198]
                            # annot['rnahybrid_altbestmir'] = md_utilities.format_mirs(record[212])
                            # annot['rnahybrid_altbestscore'] = record[211]
                        except Exception:
                            annot['dbmts'] = "{0} {1}".format(record, md_utilities.external_tools['dbMTS']['version'])
                        
                # CADD
                if variant_features['dna_type'] == 'substitution':
                    if variant_features['prot_type'] != 'missense':
                        # specific file for CADD
                        record = md_utilities.get_value_from_tabix_file('CADD', md_utilities.local_files['cadd']['abs_path'], var, variant_features)
                        if isinstance(record, str):
                            annot['cadd'] = "{0} {1}".format(record, md_utilities.external_tools['CADD']['version'])
                        else:
                            annot['cadd_raw'] = format(float(record[int(md_utilities.external_tools['CADD']['raw_col'])]), '.2f')
                            annot['cadd_phred'] = format(float(record[int(md_utilities.external_tools['CADD']['phred_col'])]), '.2f')
                            # annot['cadd_raw'] = record[4]
                            # annot['cadd_phred'] = record[5]
                else:
                    record = md_utilities.get_value_from_tabix_file('CADD', md_utilities.local_files['cadd_indels']['abs_path'], var, variant_features)
                    if isinstance(record, str):
                        annot['cadd'] = "{0} {1}".format(record, md_utilities.external_tools['CADD']['version'])
                    else:
                        annot['cadd_raw'] = record[int(md_utilities.external_tools['CADD']['raw_col'])]
                        annot['cadd_phred'] = record[int(md_utilities.external_tools['CADD']['phred_col'])]
                        # annot['cadd_raw'] = record[4]
                        # annot['cadd_phred'] = record[5]
                # spliceAI 1.3
                # ##INFO=<ID=SpliceAI,Number=.,Type=String,Description="SpliceAIv1.3 variant annotation.
                # These include delta scores (DS) and delta positions (DP) for acceptor gain (AG), acceptor loss (AL), donor gain (DG), and donor loss (DL).
                # Format: ALLELE|SYMBOL|DS_AG|DS_AL|DS_DG|DS_DL|DP_AG|DP_AL|DP_DG|DP_DL">
                spliceai_res = False
                if variant_features['dna_type'] == 'substitution':
                    record = md_utilities.get_value_from_tabix_file('spliceAI', md_utilities.local_files['spliceai_snvs']['abs_path'], var, variant_features)
                    spliceai_res = True
                elif ((variant_features['dna_type'] == 'insertion' or
                        variant_features['dna_type'] == 'duplication') and
                        variant_features['variant_size'] == 1) or \
                        (variant_features['dna_type'] == 'deletion' and
                            variant_features['variant_size'] <= 4):
                    record = md_utilities.get_value_from_tabix_file('spliceAI', md_utilities.local_files['spliceai_indels']['abs_path'], var, variant_features)
                    # print(record)
                    spliceai_res = True
                if spliceai_res is True:
                    if isinstance(record, str):
                        annot['spliceai'] = "{0} {1}".format(record, md_utilities.external_tools['spliceAI']['version'])
                    else:
                        spliceais = re.split(r'\|', record[7])
                        # ALLELE|SYMBOL|DS_AG|DS_AL|DS_DG|DS_DL|DP_AG|DP_AL|DP_DG|DP_DL
                        splicing_radar_labels.extend(['SpliceAI Acc Gain', 'SpliceAI Acc Loss', 'SpliceAI Donor Gain', 'SpliceAI Donor Loss'])
                        order_list = ['DS_AG', 'DS_AL', 'DS_DG', 'DS_DL', 'DP_AG', 'DP_AL', 'DP_DG', 'DP_DL']
                        i = 2
                        for tag in order_list:
                            identifier = "spliceai_{}".format(tag)
                            annot[identifier] = spliceais[i]
                            if re.search(r'DS_', tag):
                                splicing_radar_values.append(spliceais[i])
                            i += 1
                            if re.match('spliceai_DS_', identifier):
                                id_color = "{}_color".format(identifier)
                                annot[id_color] = md_utilities.get_spliceai_color(float(annot[identifier]))
                                if 'mpa_score' not in annot or annot['mpa_score'] < 10:
                                    if float(annot[identifier]) > md_utilities.predictor_thresholds['spliceai_max']:
                                        annot['mpa_score'] = 10
                                        annot['mpa_impact'] = 'high splice'
                                    elif float(annot[identifier]) > md_utilities.predictor_thresholds['spliceai_mid']:
                                        annot['mpa_score'] = 8
                                        annot['mpa_impact'] = 'moderate splice'
                                    elif float(annot[identifier]) > md_utilities.predictor_thresholds['spliceai_min']:
                                        annot['mpa_score'] = 6
                                        annot['mpa_impact'] = 'low splice'

            elif var['genome_version'] == 'hg19':
                # gnomad ex
                record = md_utilities.get_value_from_tabix_file('gnomAD exome', md_utilities.local_files['gnomad_exome']['abs_path'], var, variant_features)
                if isinstance(record, str):
                    annot['gnomad_exome_all'] = record
                else:
                    annot['gnomad_exome_all'] = record[int(md_utilities.external_tools['gnomAD']['annovar_format_af_col'])]
                # gnomad ge
                record = md_utilities.get_value_from_tabix_file('gnomAD genome', md_utilities.local_files['gnomad_genome']['abs_path'], var, variant_features)
                if isinstance(record, str):
                    annot['gnomad_genome_all'] = record
                else:
                    annot['gnomad_genome_all'] = record[int(md_utilities.external_tools['gnomAD']['annovar_format_af_col'])]
                # clinpred
                if variant_features['prot_type'] == 'missense':
                #     record = md_utilities.get_value_from_tabix_file('ClinPred', md_utilities.local_files['clinpred']['abs_path'], var, variant_features)
                #     if isinstance(record, str):
                #         annot['clinpred_score'] = record
                #     else:
                #         annot['clinpred_score'] = record[4]
                #     annot['clinpred_color'] = "#000000"
                #     annot['clinpred_pred'] = 'no prediction'
                #     if re.search(r'^[\d\.]+$', annot['clinpred_score']):
                #         annot['clinpred_score'] = format(float(annot['clinpred_score']), '.2f')
                #         annot['clinpred_color'] = md_utilities.get_preditor_single_threshold_color(annot['clinpred_score'], 'clinpred')
                #         annot['clinpred_pred'] = 'Tolerated'
                #         if float(annot['clinpred_score']) > md_utilities.predictor_thresholds['clinpred']:
                #             annot['clinpred_pred'] = 'Damaging'
                    # mistic
                    record = md_utilities.get_value_from_tabix_file('Mistic', md_utilities.local_files['mistic']['abs_path'], var, variant_features)
                    if isinstance(record, str):
                        annot['mistic_score'] = record
                    else:
                        annot['mistic_score'] = record[4]
                    annot['mistic_color'] = "#000000"
                    annot['mistic_pred'] = 'no prediction'
                    if re.search(r'^[\d\.]+$', annot['mistic_score']):
                        annot['mistic_score'] = format(float(annot['mistic_score']), '.2f')
                        annot['mistic_color'] = md_utilities.get_preditor_single_threshold_color(annot['mistic_score'], 'mistic')
                        annot['mistic_pred'] = 'Tolerated'
                        if float(annot['mistic_score']) > md_utilities.predictor_thresholds['mistic']:
                            annot['mistic_pred'] = 'Damaging'
                if variant_features['dna_type'] == 'substitution':
                    # dbscSNV
                    record = md_utilities.get_value_from_tabix_file('dbscSNV', md_utilities.local_files['dbscsnv']['abs_path'], var, variant_features)
                    if isinstance(record, str):
                        annot['dbscsnv_ada'] = "{0} {1}".format(record, md_utilities.external_tools['dbscSNV']['version'])
                        annot['dbscsnv_rf'] = "{0} {1}".format(record, md_utilities.external_tools['dbscSNV']['version'])
                        if annot['dbscsnv_ada'] != 'No match in dbscSNV v1.1':
                            splicing_radar_labels.append('dbscSNV ADA')
                            splicing_radar_values.append(annot['dbscsnv_ada'])
                        if annot['dbscsnv_rf'] != 'No match in dbscSNV v1.1':
                            splicing_radar_labels.append('dbscSNV RF')
                            splicing_radar_values.append(annot['dbscsnv_rf'])
                    else:
                        try:
                            annot['dbscsnv_ada'] = "{:.2f}".format(float(record[14]))
                            annot['dbscsnv_ada_color'] = md_utilities.get_preditor_single_threshold_color(float(annot['dbscsnv_ada']), 'dbscsnv')
                            if annot['dbscsnv_ada'] != 'No match in dbscSNV v1.1':
                                splicing_radar_labels.append('dbscSNV ADA')
                                splicing_radar_values.append(annot['dbscsnv_ada'])
                        except Exception:
                            # "score" is '.'
                            annot['dbscsnv_ada'] = "No score for dbscSNV ADA {}".format(md_utilities.external_tools['dbscSNV']['version'])
                        try:
                            annot['dbscsnv_rf'] = "{:.2f}".format(float(record[15]))
                            annot['dbscsnv_rf_color'] = md_utilities.get_preditor_single_threshold_color(float(annot['dbscsnv_rf']), 'dbscsnv')
                            if annot['dbscsnv_rf'] != 'No match in dbscSNV v1.1':
                                splicing_radar_labels.append('dbscSNV RF')
                                splicing_radar_values.append(annot['dbscsnv_rf'])
                        except Exception:
                            # "score" is '.'
                            annot['dbscsnv_rf'] = "No score for dbscSNV RF {}".format(md_utilities.external_tools['dbscSNV']['version'])
                        # dbscsnv_mpa_threshold = 0.8
                        if 'mpa_score' not in annot or annot['mpa_score'] < 10:
                            if (isinstance(annot['dbscsnv_ada'], float) and
                                float(annot['dbscsnv_ada']) > md_utilities.predictor_thresholds['dbscsnv']) or \
                                (isinstance(annot['dbscsnv_rf'], float) and
                                 float(annot['dbscsnv_ada']) > md_utilities.predictor_thresholds['dbscsnv']):
                                annot['mpa_score'] = 10
                                annot['mpa_impact'] = 'high splice'
        # get classification info
        curs.execute(
            "SELECT a.acmg_class, a.class_date, a.comment, b.id, b.email, b.email_pref, b.username, c.html_code, c.acmg_translation \
                FROM class_history a, mobiuser b, valid_class c WHERE a.mobiuser_id = b.id AND a.acmg_class = c.acmg_class \
                AND a.variant_feature_id = %s ORDER BY a.class_date ASC",
            (variant_id,)
        )
        class_history = curs.fetchall()
        if len(class_history) == 0:
            class_history = None
        # MaxEntScan
        # we need to iterize through the wt and mt sequences to get
        # stretches of 23 nts for score3 and of 9 nts for score 5
        # then we create 2 NamedTemporaryFile to store the data
        # then run the perl scripts as subprocesses like this:
        # import subprocess
        # result = subprocess.run(['perl', 'score5.pl', 'test5'], stdout=subprocess.PIPE)
        # result.stdout
        # result is like
        # b'CAAATTCTG\t-17.88\nAAATTCTGC\t-13.03\nAATTCTGCA\t-35.61\nATTCTGCAA\t-22.21\nTTCTGCAAT\t-31.16\nTCTGCAATC\t-13.69\nCTGCAATCC\t-14.15\nTGCAATCCT\t-37.49\nGCAATCCTC\t-30.00\n'

        # iterate through scores and get the most likely to disrupt splicing
        # from Houdayer Humut 2012
        # "In every case, we recommend first‐line analysis with MES using a 15% cutoff."
        signif_scores5 = signif_scores3 = None
        # print(pos_splice_site )
        scores5wt, seq5wt_html = md_utilities.maxentscan(9, variant_features['variant_size'], variant_features['wt_seq'], 5)
        scores5mt, seq5mt_html = md_utilities.maxentscan(9, variant_features['variant_size'], variant_features['mt_seq'], 5)
        # scores5mt = md_utilities.maxentscan(9, variant_features['variant_size'], variant_features['mt_seq'], 5)
        signif_scores5 = md_utilities.select_mes_scores(
            re.split('\n', scores5wt),
            seq5wt_html,
            re.split('\n', scores5mt),
            seq5mt_html,
            0.15,
            3
        )
        if signif_scores5 == {}:
            signif_scores5 = None
        # 2 last numbers are variation cutoff to sign a significant change and absolute threshold to consider a score as interesting
        # print(signif_scores5)
        # ex
        # {5: ['CAGGTAATG', '9.43', 'CAGATAATG', '1.25', -654.4, 'CAG<span class="w3-text-red"><strong>G</strong></span>TAATG\n',\
        # '<strong>CAG</strong>gtaatg', 'CAG<span class="w3-text-red"><strong>A</strong></span>TAATG\n', '<strong>CAG</strong>ataatg']}
        if (variant_features['start_segment_type'] != 'exon') or \
                (annot['nearest_site_type'] == 'acceptor' and
                    annot['nearest_site_dist'] < 10):
            # exonic variants not near 3' ss don't require predictions for 3'ss
            seq3wt, seq3wt_html = md_utilities.maxentscan(23, variant_features['variant_size'], variant_features['wt_seq'], 3)
            seq3mt, seq3mt_html = md_utilities.maxentscan(23, variant_features['variant_size'], variant_features['mt_seq'], 3)
            signif_scores3 = md_utilities.select_mes_scores(
                re.split('\n', md_utilities.maxentscan(23, variant_features['variant_size'], variant_features['wt_seq'], 3)[0]),
                seq3wt_html,
                re.split('\n', md_utilities.maxentscan(23, variant_features['variant_size'], variant_features['mt_seq'], 3)[0]),
                seq3mt_html,
                0.15,
                3
            )
            if signif_scores3 == {}:
                signif_scores3 = None
            # print(signif_scores3)
        else:
            signif_scores3 = 'Not performed'
    else:
        close_db()
        return render_template('md/unknown.html', run_mode=md_utilities.get_running_mode(), query="variant id: {}".format(variant_id))
    close_db()
    if 'mpa_score' not in annot:
        annot['mpa_score'] = 0
        annot['mpa_impact'] = 'unknown'
    else:
        annot['mpa_color'] = md_utilities.get_preditor_double_threshold_color(annot['mpa_score'], 'mpa_mid', 'mpa_max')
    return render_template(
        'md/variant.html', run_mode=md_utilities.get_running_mode(), favourite=favourite, var_cname=var_cname, aa_pos=aa_pos,
        splicing_radar_labels=splicing_radar_labels, splicing_radar_values=splicing_radar_values,
        urls=md_utilities.urls, external_tools=md_utilities.external_tools, thresholds=md_utilities.predictor_thresholds,
        variant_features=variant_features, variant=variant, protein_domain=domain,
        class_history=class_history, annot=annot, mes5=signif_scores5, mes3=signif_scores3
    )

# -------------------------------------------------------------------
# web app - search engine


@bp.route('/search_engine', methods=['POST'])
def search_engine():
    # print("--{}--".format(request.form['search']))
    query_engine = request.form['search']
    # query_engine = query_engine.upper()
    error = None
    variant_regexp = md_utilities.regexp['variant']
    variant_regexp_flexible = md_utilities.regexp['variant_flexible']
    amino_acid_regexp = md_utilities.regexp['amino_acid']
    nochr_captured_regexp = md_utilities.regexp['nochr_captured']

    if query_engine is not None and \
            query_engine != '':
        pattern = ''
        query_type = ''
        sql_table = 'variant_feature'
        col_names = 'id, c_name, gene_name, p_name'
        semaph_query = 0
        # deal w/ protein names
        query_engine = re.sub(r'\s', '', query_engine)
        if re.search(r'^last$', query_engine):
            # get variant annotated the last 7 days
            if 'db' not in locals():
                db = get_db()
                curs = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
                curs.execute(
                    "SELECT a.id, a.c_name, a.p_name, a.gene_name, a.creation_user, a.creation_date, b.nm_version, c.username from variant_feature a, gene b, mobiuser c \
                    WHERE a.gene_name = b.name AND a.creation_user = c.id AND a.creation_date > CURRENT_DATE - 7 ORDER BY creation_date DESC"
                )
                variants = curs.fetchall()
                return render_template('md/variant_multiple.html', variants=variants)
        match_object = re.search(rf'^([{amino_acid_regexp}]{{1}})(\d+)([{amino_acid_regexp}\*]{{1}})$', query_engine)  # e.g. R34X
        if match_object:
            confusing_genes = ['C1R', 'C8G', 'S100G', 'F11R', 'C1S', 'A2M', 'C1D', 'S100P', 'F2R', 'C8A', 'C4A']
            # list of genes that look like the query
            if query_engine.upper() in confusing_genes:
                sql_table = 'gene'
                query_type = 'name[1]'
                col_names = 'name'
                pattern = query_engine.upper()
            else:
                query_type = 'p_name'
                pattern = md_utilities.one2three_fct(query_engine)
        elif re.search(r'^rs\d+$', query_engine):
            query_type = 'dbsnp_id'
            match_object = re.search(r'^rs(\d+)$', query_engine)
            pattern = match_object.group(1)
        elif re.search(r'^p\..+', query_engine):
            query_type = 'p_name'
            var = md_utilities.clean_var_name(query_engine)
            match_object = re.search(r'^(\w{1})(\d+)([\w\*]{1})$', var)  # e.g. p.R34X
            match_object_inter = re.search(r'^(\w{1})(\d+)([d][ue][pl])', var)  # e.g. p.L34del
            match_object_long = re.search(r'^(\w{1})(\d+_)(\w{1})(\d+.+)$', var)  # e.g. p.R34_E68del
            if match_object or match_object_inter or match_object_long:
                pattern = md_utilities.one2three_fct(var)
            else:
                if re.search('X', var):
                    pattern = re.sub(r'X', 'Ter', var)
                elif re.search(r'\*', var):
                    pattern = re.sub(r'\*', 'Ter', var)
                else:
                    pattern = var
        elif re.search(rf'^[Nn][Mm]_\d+\.\d+:c\.{variant_regexp}$', query_engine) or \
                re.search(rf'^[Nn][Mm]_\d+\.\d+\([A-Za-z0-9-]+\):c\.{variant_regexp}$', query_engine):  # NM acc_no variant
            # f-strings usage https://stackoverflow.com/questions/6930982/how-to-use-a-variable-inside-a-regular-expression
            # API call
            if 'db' not in locals():
                db = get_db()
                curs = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
            api_key = md_utilities.get_api_key(g, curs)
            match_obj = re.search(rf'^([Nn][Mm]_\d+\.\d+)\(*[A-Za-z0-9-]*\)*(:c\.{variant_regexp})$', query_engine)
            if api_key is not None:
                return redirect(url_for('api.api_variant_create', variant_chgvs='{0}{1}'.format(match_obj.group(1), match_obj.group(2)), caller='browser', api_key=api_key), code=307)
        elif re.search(r'^[Nn][Mm]_\d+', query_engine):  # NM acc_no
            sql_table = 'gene'
            query_type = 'name[2]'
            col_names = 'name'
            match_object = re.search(r'^([Nn][Mm]_\d+)\.?\d?', query_engine)
            pattern = match_object.group(1)
        elif re.search(rf'^[Nn][Cc]_0000\d{{2}}\.\d{{1,2}}:g\.{variant_regexp}$', query_engine):  # strict HGVS genomic
            sql_table = 'variant'
            query_type = 'g_name'
            col_names = 'feature_id'
            db = get_db()
            match_object = re.search(rf'^([Nn][Cc]_0000\d{{2}}\.\d{{1,2}}):g\.({variant_regexp})$', query_engine)
            # res_common = md_utilities.get_common_chr_name(db, match_object.group(1))
            chrom = md_utilities.get_common_chr_name(db, match_object.group(1))[0]
            pattern = match_object.group(2)
            # res_common = md_utilities.get_common_chr_name(db, )
        elif re.search(rf'^[Nn][Cc]_0000\d{{2}}\.\d{{1,2}}:g\.{variant_regexp};[\w-]+$', query_engine):  # strict HGVS genomic + gene (API call)
            # API call
            if 'db' not in locals():
                db = get_db()
                curs = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
            api_key = md_utilities.get_api_key(g, curs)
            match_obj = re.search(rf'^([Nn][Cc]_0000\d{{2}}\.\d{{1,2}}:g\.{variant_regexp});([\w-]+)$', query_engine)
            if api_key is not None:
                return redirect(url_for('api.api_variant_g_create', variant_ghgvs=match_obj.group(1), gene_hgnc=match_obj.group(2), caller='browser', api_key=api_key), code=307)
        elif re.search(rf'^[Cc][Hh][Rr]({nochr_captured_regexp}):g\.{variant_regexp_flexible}$', query_engine):  # deal w/ genomic
            sql_table = 'variant'
            query_type = 'g_name'
            col_names = 'feature_id'
            match_object = re.search(rf'^[Cc][Hh][Rr]({nochr_captured_regexp}):g\.({variant_regexp_flexible})$', query_engine)
            chrom = match_object.group(1)
            pattern = match_object.group(2)
            # if re.search(r'>', pattern):
            #    pattern = pattern.upper()
        elif re.search(r'^g\..+', query_engine):  # g. ng dna vars
            query_type = 'ng_name'
            pattern = md_utilities.clean_var_name(query_engine)
        elif re.search(r'^c\..+', query_engine):  # c. dna vars
            query_type = 'c_name'
            pattern = md_utilities.clean_var_name(query_engine)
        elif re.search(r'^%\d+$', query_engine):  # only numbers: get matching variants (exact position match) - specific query
            match_obj = re.search(r'^%(\d+)$', query_engine)
            pattern = match_obj.group(1)
            db = get_db()
            curs = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
            curs.execute(
                r"SELECT {0} FROM {1} WHERE c_name ~ '^{2}[^\d]' OR c_name ~ '_{2}[^\d]' OR p_name ~ '^{2}[^\d]' OR p_name ~ '_{2}[^\d]'".format(
                    col_names, sql_table, pattern
                )
            )
            semaph_query = 1
        elif re.search(r'^\d{2,}$', query_engine):  # only numbers: get matching variants (partial match, at least 2 numbers) - specific query
            db = get_db()
            curs = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
            curs.execute(
                "SELECT {0} FROM {1} WHERE c_name LIKE '%{2}%' OR p_name LIKE '%{2}%'".format(
                    col_names, sql_table, query_engine
                )
            )
            semaph_query = 1
        elif re.search(r'^[A-Za-z0-9-]+$', query_engine):  # genes
            sql_table = 'gene'
            query_type = 'name[1]'
            col_names = 'name'
            pattern = query_engine
            if not re.search(r'[oO][rR][fF]', pattern):
                pattern = pattern.upper()
            else:
                # from experience the only low case in gene is 'orf'
                pattern = re.sub(r'O', 'o', pattern)
                pattern = re.sub(r'R', 'r', pattern)
                pattern = re.sub(r'F', 'f', pattern)
                pattern = pattern.capitalize()
        else:
            error = 'Sorry I did not understand this query ({}).'.format(query_engine)
            # return render_template('md/unknown.html', query=query_engine, transformed_query=pattern)
        if error is None:
            # print(semaph_query)
            if semaph_query == 0:
                if 'db' not in locals():
                    db = get_db()
                curs = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
                # sql_query = "SELECT {0} FROM {1} WHERE {2} = '{3}'".format(col_names, sql_table, query_type, pattern)
                # return render_template('md/search_engine.html', query=text)
                # a little bit of formatting
                if re.search('variant', sql_table) and \
                        re.search('>', pattern):
                    # upper for the end of the variant, lower for genomic chr
                    var_match = re.search(r'^(.*)(\d+)([ACTGactg]>[ACTGactg])$', pattern)
                    if var_match:
                        pattern = var_match.group(1).lower() + var_match.group(2) + var_match.group(3).upper()
                    else:
                        error = 'You submitted a forbidden character in "{}".'.format(pattern)
                        flash(error, 'w3-pale-red')
                        return render_template('md/unknown.html', run_mode=app.config['RUN_MODE'])
                    # print(pattern)
                if pattern == 'g_name':
                    curs.execute(
                        "SELECT {0} FROM {1} WHERE chr = {2} AND {3} = '{4}'".format(
                            col_names, sql_table, chrom, query_type, pattern
                        )
                    )
                else:
                    curs.execute(
                        "SELECT {0} FROM {1} WHERE {2} = '{3}'".format(
                            col_names, sql_table, query_type, pattern
                        )
                    )
                result = None
            if sql_table == 'gene':
                result = curs.fetchone()
                if result is None:
                    query_type = 'second_name'
                    # https://www.postgresql.org/docs/9.3/functions-matching.html#POSIX-ESCAPE-SEQUENCES
                    curs.execute(
                        "SELECT {0} FROM {1} WHERE {2} ~* '\m[;,]?{3}[;,]?\M'".format(
                            col_names, sql_table, query_type, pattern
                        )
                    )
                    result_second = curs.fetchone()
                    if result_second is None:
                        close_db()
                        error = 'Sorry the gene does not seem to exist yet in MD or cannot be annotated for some reason ({}).'.format(query_engine)
                    else:
                        return redirect(url_for('md.gene', gene_name=result_second[col_names][0]))
                else:
                    return redirect(url_for('md.gene', gene_name=result[col_names][0]))
            else:
                result = curs.fetchall()
                if not result:
                    if query_type == 'dbsnp_id':
                        # api call to create variant from rs id
                        api_key = md_utilities.get_api_key(g, curs)
                        if api_key is not None:
                            close_db()
                            return redirect(url_for('api.api_variant_create_rs', rs_id='rs{}'.format(pattern), caller='browser', api_key=api_key), code=307)
                    error = 'Sorry the variant or gene does not seem to exist yet in MD or cannot be annotated for some reason ({}).<br /> \
                            You can annotate it directly at the corresponding gene page.'.format(query_engine)
                else:
                    close_db()
                    if len(result) == 1:
                        return redirect(url_for('md.variant', variant_id=result[0][0]))
                    else:
                        return render_template('md/variant_multiple.html', run_mode=md_utilities.get_running_mode(), variants=result)
    else:
        error = 'Please type something for the search engine to work.'
    flash(error, 'w3-pale-red')
    return render_template('md/unknown.html', run_mode=md_utilities.get_running_mode())
