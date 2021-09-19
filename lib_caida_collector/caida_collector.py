import logging
from pathlib import Path
from typing import List

import bz2

from lib_utils import base_classes, file_funcs, helper_funcs

from .base_as import AS
from .bgp_dag import BGPDAG
from .customer_provider_link import CustomerProviderLink as CPLink
from .peer_link import PeerLink


class CaidaCollector(base_classes.Base):
    """Downloads relationships, determines metadata, and inserts to db"""

    def __init__(self,
                 *args,
                 BaseASCls=AS,
                 GraphCls=BGPDAG,
                 cache_dir=Path("/tmp/caida_collector_cache"),
                 **kwargs):
        super(CaidaCollector, self).__init__(*args, **kwargs)
        self.BaseASCls = BaseASCls
        self.GraphCls = GraphCls
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def run(self, cache=True):
        """Downloads relationships, parses data, and inserts into the db.

        https://publicdata.caida.org/datasets/as-relationships/serial-2/

        Can specify a download time if you want to download an older dataset
        """

        file_lines = self._read_file(cache)
        cp_links, peer_links, ixps, input_clique = self._get_ases(file_lines)
        bgp_dag = self.GraphCls(cp_links,
                                peer_links,
                                ixps=ixps,
                                input_clique=input_clique,
                                BaseASCls=self.BaseASCls)
        self._write_tsv(bgp_dag)
        return bgp_dag

######################
# File reading funcs #
######################

    def _read_file(self, cache: bool) -> List[str]:
        """Reads the file from the URL and unzips it and returns the lines"""

        cache_path = self.cache_dir / self.dl_time.strftime("%Y.%m.%d.txt")

        if not cache_path.exists() or cache is False:
            self._write_cache_file(cache_path)

        with cache_path.open(mode="r") as f:
            return [x.strip() for x in f.readlines()]

    def _write_cache_file(self, cache_path: Path):
        """Writes the downloaded file to the cache"""

        logging.info("No file cached from Caida. Downloading Caida file now")
        url = self._get_url()

        path_str = str(self._dir / "download.bz2")
        # Create a temp path for the bz2
        with file_funcs.temp_path(path_str=path_str) as path:
            file_funcs.download_file(url, path)
            # Unzip and read
            with bz2.open(path) as f:
                # Decode bytes into str
                data = [x.decode() for x in f.readlines()]
        # Write the file to the cache path
        with cache_path.open(mode="w") as f:
            for line in data:
                f.write(line)

    def _get_url(self) -> str:
        """Gets urls to download relationship files"""

        # Api url
        prepend = 'http://data.caida.org/datasets/as-relationships/serial-2/'
        # Gets all URLs. Keeps only the link for the proper download time
        return [prepend + x for x in helper_funcs.get_hrefs(prepend)
                if self.dl_time.strftime("%Y%m01") in x][0]

########################
# Graph building funcs #
########################

    def _get_ases(self, lines: List[str]):
        """Fills the initial AS dict and adds the following info:

        Creates AS dict with peers, providers, customers, input clique, ixps
        """

        input_clique = set()
        ixps = set()
        # Customer provider links
        cp_links = set()
        # Peer links
        peer_links = set()
        for line in lines:
            # Get Caida input clique. See paper on site for what this is
            if line.startswith("# input clique"):
                self._extract_input_clique(line, input_clique)
            # Get detected Caida IXPs. See paper on site for what this is
            elif line.startswith("# IXP ASes"):
                self._extract_ixp_ases(line, ixps)
            # Not a comment, must be a relationship
            elif not line.startswith("#"):
                # Extract all customer provider pairs
                if "-1" in line:
                    self._extract_provider_customers(line, cp_links)
                # Extract all peers
                else:
                    self._extract_peers(line, peer_links)
        return cp_links, peer_links, ixps, input_clique

    def _extract_input_clique(self, line: str, input_clique: set):
        """Adds all ASNs within input clique line to ases dict"""

        # Gets all input ASes for clique
        for asn in line.split(":")[-1].strip().split(" "):
            # Insert AS into graph
            input_clique.add(int(asn))

    def _extract_ixp_ases(self, line: str, ixps: set):
        """Adds all ASNs that are detected IXPs to ASes dict"""

        # Get all IXPs that Caida lists
        for asn in line.split(":")[-1].strip().split(" "):
            ixps.add(int(asn))

    def _extract_provider_customers(self, line: str, cp_links: set):
        """Extracts provider customers: <provider-as>|<customer-as>|-1"""

        provider_asn, customer_asn, _, source = line.split("|")
        cp_links.add(CPLink(customer_asn=int(customer_asn),
                            provider_asn=int(provider_asn)))

    def _extract_peers(self, line: str, peer_links: set):
        """Extracts peers: <peer-as>|<peer-as>|0|<source>"""

        peer1_asn, peer2_asn, _, source = line.split("|")
        peer_links.add(PeerLink(int(peer1_asn), int(peer2_asn)))

    def _write_tsv(self, bgp_dag):
        """Writes BGP DAG info to a TSV"""

        logging.info("Made graph. Now writing to TSV")
        rows = []
        for x in bgp_dag.as_dict.values():
            rows.append(x.db_row)
        file_funcs.write_dicts_to_tsv(rows, self.tsv_path)
        logging.debug("Wrote TSV")


