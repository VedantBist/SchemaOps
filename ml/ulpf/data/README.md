# Bundled data

`dbip-country-lite.csv.gz` is the **IP to Country Lite** database by [DB-IP](https://db-ip.com), licensed under
[Creative Commons Attribution 4.0](https://creativecommons.org/licenses/by/4.0/). It is shipped inside the image so
GeoIP enrichment works in air-gapped networks with no runtime download. To refresh it, replace the file with a
newer monthly release (or point `ULPF_GEOIP_FILE` at another copy in the same `start,end,country` CSV format).
